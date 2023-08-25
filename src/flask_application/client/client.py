import os

import valclient


class RegionError(Exception):
    '''
      Raised whenever there's a problem while attempting to fetch the region.
    '''
    pass


class CustomClient(valclient.Client):
    """Is a valclient that don't need a region"""

    def __init__(self, region=None, auth=None):
        if file_region := self.get_region():
            super().__init__(file_region, auth)
            return
        if region:
            super().__init__(region, auth)
            return
        raise RegionError('Unable to get region')

    def get_region(self):
        # BUG: this function is not finding the region
        try:
            with open(os.path.join(os.getenv('LOCALAPPDATA'), R'VALORANT\Saved\Logs\ShooterGame.log'), 'rb') as f:
                lines = f.readlines()
            for line in lines:
                if b'regions' in line:
                    region = line.split(b'regions/')[1].split(b']')[0]
                    return region.decode()
        except Exception:
            return ''
