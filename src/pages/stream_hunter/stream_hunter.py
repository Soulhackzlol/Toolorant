import asyncio
import logging
import urllib.parse
import aiohttp
import requests
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import game_resources as gr
from client import CustomClient
from settings import user_settings
from . import platforms
from .player import Player
import os

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

# TODO: use proxies to make requests so the IP is not blocked
def get_proxies():
    proxies_response = requests.get(
        'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=5000&country=all&simplified=true',
        stream=True
    )
    return [f"socks4://{proxy.decode().strip()}" for proxy in proxies_response.iter_lines() if proxy]

# Function to check Twitch link using a new browser instance
async def check_twitch_link(username, tag):
    encoded_username = urllib.parse.quote(username)
    encoded_tag = urllib.parse.quote(tag)
    url = f"https://tracker.gg/valorant/profile/riot/{encoded_username}%23{encoded_tag}/overview"
    
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-client-side-phishing-detection")
    chrome_options.add_argument("--disable-crash-reporter")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36")
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-infobars')
    
    driver = uc.Chrome(options=chrome_options)

    try:
        logger.info(f"Navigating to URL: {url}")
        driver.get(url)

        # Wait for the page to load completely
        await asyncio.sleep(5)  # Adjust sleep time if necessary

        content = driver.page_source
        logger.info("Page content fetched")
        soup = BeautifulSoup(content, 'html.parser')
        
        # Refined selectors for Twitch link
        selectors = [
            'a.trn-button--platform-twitch',
            'a.trn-button.trn-button--circle.trn-button--platform-twitch',
            'a[href*="twitch.tv"]',
        ]

        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                twitch_url = element['href']
                logger.info(f"Twitch account linked found with selector: {selector}, URL: {twitch_url}")
                return twitch_url

        logger.info(f"No Twitch account linked for {username}#{tag}")
        return None

    except Exception as e:
        logger.error(f"Failed to fetch the profile page for {username}#{tag} due to {str(e)}")
        return None

    finally:
        driver.quit()

class StreamHunter:
    def __init__(self, client: CustomClient) -> None:
        super().__init__()
        self.client = client
        self.platforms = {'twitch': platforms.Twitch}
        self._seen_matches = {}

    async def get_enemies(self, match_info: dict) -> list[Player]:
        for player in match_info['Players']:
            if player['Subject'] == self.client.puuid:
                ally_team = player['TeamID']
                break
        enemies = [player for player in match_info['Players'] if player['TeamID'] != ally_team]
        
        async with aiohttp.ClientSession() as session:
            tasks = [
                asyncio.create_task(self.client.a_get_player_full_name(session, enemy['Subject']))
                for enemy in enemies
            ]
            results = await asyncio.gather(*tasks)
        
        return [Player(full_name, gr.Agent(enemy['CharacterID'].lower())) for full_name, enemy in zip(results, enemies)]

    async def get_player_streams(self, player: Player) -> list[str]:
        logger.debug(f'Getting streams of player {player.full_name}')
        streams = []
        async with aiohttp.ClientSession() as session:
            for platform_name, platform_cls in self.platforms.items():
                platform = platform_cls(session, **user_settings.stream_hunter[platform_name])
                await platform.initialize()
                tasks = [
                    asyncio.create_task(platform.get_response(name))
                    for name in player.name_variations
                ]
                responses = await asyncio.gather(*tasks)
                for response in responses:
                    if live := platform.get_live(response):
                        logger.info(f"Live stream found for player {player.full_name} on {platform_name}")
                        streams.extend(live)
                        break  # Exit the loop if a live stream is found

            if not streams:
                # Check Twitch link on Tracker.gg profile only if no live stream was found
                username, tag = player.full_name.split('#')
                twitch_link = await check_twitch_link(username, tag)
                if twitch_link:
                    streams.append(twitch_link)

        logger.info(f"Streams for player {player.full_name}: {streams}")
        return list(set(streams))

    async def hunt_async(self) -> dict[tuple[str, str], list[str]]:
        logger.info("Starting hunt")
        try:
            match_info = self.client.coregame_fetch_match()
            logger.info("Match information fetched")
        except Exception as e:
            logger.error(f'Match information could not be fetched due to error: {e}')
            return {}

        # Fetch an existing player's data
        enemies = await self.get_enemies(match_info)
        
        logger.info('Getting players streams')

        tasks = [
            self.get_player_streams(player)
            for player in enemies
        ]
        results = await asyncio.gather(*tasks)

        result = {
            (player.name, player.agent.name): streams
            for player, streams in zip(enemies, results)
        }

        # Force close all Chrome instances
        # Sorry if you use chrome but doing driver.quit doesnt work smh
        try:
            os.system("taskkill /f /im chrome.exe")
        except Exception as e:
            logger.error(f"Failed to force close Chrome processes: {str(e)}")

        # Save result on cache and return
        self._seen_matches[match_info['MatchID']] = result
        logger.info("Hunt result:")
        for k, v in result.items():
            logger.info(f"{k}: {v}")
        return result

    def hunt(self) -> dict[tuple[str, str], list[str]]:
        return asyncio.run(self.hunt_async())
