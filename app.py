#   Copyright (C) 2018 Lunatixz
# This file is part of Locast.
#
# Locast is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Locast is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Locast.  If not, see <http://www.gnu.org/licenses/>.

" Flask app to act as middle man between tvheadend and locast.org "

# -*- coding: utf-8 -*-
import os
import sys
import datetime
import json
import logging
import time
import threading

from flask import Flask
from flask import Response, request, abort

import requests
import requests_cache

import xmltv

# Global Config
requests_cache.install_cache('/locastcfg/locast', expire_after=900)
logging.basicConfig(level=logging.DEBUG)
logging.getLogger(__name__)

## GLOBALS ##
APP = Flask(__name__)
DEBUG = True
GEO_URL = 'http://ip-api.com/json'
BASE_URL = 'https://www.locast.org'
BASE_API = BASE_URL + '/wp/wp-admin/admin-ajax.php'
EPG_FILE = '/locastcfg/locast-epg.xml'
EPG_SOCK = '/tvhconfig/epggrab/xmltv.sock'

class Locast():
    " Manage connections with login info for locast.org "
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        #self.lat, self.lon = self.set_region()
        self.lat = 38.9885
        self.lon = -76.791
        self.primary_dma = ''
        self.loc_name = ''
        self.user_email = ''
        self.password = ''
        self.token = ''

        if not self.env_load_config():
            self.logger.error('Config env variables not found. Exiting now.')
            sys.exit()
        if not self.set_city():
            self.logger.error('Could not set city. Exiting now.')
            sys.exit()
        if not self.token:
            if not self.login(self.user_email, self.password):
                self.logger.error('Unable to login. Exiting now.')
                sys.exit()
        self.logger.info(f"Running as {self.user_email} with token = {self.token}")


    def env_load_config(self):
        " Load user email, password, and token (if exists) from environment "
        self.user_email = os.environ.get('LCST_USER_EMAIL', '')
        self.password = os.environ.get('LCST_USER_PSWRD', '')
        self.token = os.environ.get('LCST_TOKEN', '')

        if not self.user_email:
            return False

        return True


    def load_config(self):
        " Load user email, password, and token (if exists) from config "
        jdata = None
        if os.path.exists('/locastcfg/locast.json'):
            with open('/locastcfg/locast.json') as fp:
                jdata = json.load(fp)

        if not jdata:
            return False

        self.user_email = jdata.get('user_email', '')
        self.password = jdata.get('password', '')
        self.token = jdata.get('token', '')

        if not self.user_email:
            return False

        return True


    def save_config(self):
        " Save user email, password, and token (if exists) from config "
        jdata = {'user_email': self.user_email,
                 'password': self.password,
                 'token': self.token}

        with open('locast.json', 'w') as fp:
            json.dump(jdata, fp)


    def build_cookies(self):
        " Build cookie dictionary for each request "
        cookies = {}
        cookies['_member_location'] = f"{self.lat}%2C{self.lon}"
        if self.primary_dma:
            cookies['_user_dma'] = self.primary_dma
            cookies['_user_location_name'] = self.loc_name
        if self.token:
            cookies['_member_token'] = self.token
            cookies['_member_username'] = self.user_email
            cookies['_member_role'] = '1'

        self.logger.debug(f"cookies: {cookies}")
        return cookies


    def build_header(self):
        " Set header values for each request "
        header_dict = {}
        header_dict['Accept'] = 'application/json, application/x-www-form-urlencoded, text/javascript, */*; q=0.01'
        header_dict['Connection'] = 'keep-alive'
        header_dict['Origin'] = BASE_URL
        header_dict['Referer'] = BASE_URL
        header_dict['User-Agent'] = 'Mozilla/5.0 (Windows NT 6.2; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/32.0.1667.0 Safari/537.36'
        return header_dict


    def login(self, user, password):
        " Obtain login token from source and save to config for next time "
        if user:
            r = requests.post(BASE_API, data={'action':'member_login', 'username':user, 'password':password},
                              cookies=self.build_cookies(),
                              headers=self.build_header())
            data = r.json()
            #{u'token': u'', u'role': 1}
            if data and 'token' in data:
                self.token = data['token']
                self.save_config()
                self.logger.info(f"Login successful.  Token = {data['token']}")
                return True
            else:
                self.logger.warning('No token received')
        return False


    def get_epg(self):
        " Get EPG data from source "
        #{
        #    "active": true,
        #    "affiliate": "CBS",
        #    "affiliateName": "CBS",
        #    "callSign": "WCBS",
        #    "dma": 501,
        #    "id": 104,
        #    "listings": [
        #        {
        #            "airdate": 1535328000000,
        #            "audioProperties": "CC, HD 1080i, HDTV, New, Stereo",
        #            "description": "Primary stories and alternative news.",
        #            "duration": 1800,
        #            "entityType": "Episode",
        #            "genres": "Newsmagazine",
        #            "isNew": true,
        #            "programId": "EP000191906491",
        #            "showType": "Series",
        #            "startTime": 1535410800000,
        #            "stationId": 104,
        #            "title": "Inside Edition",
        #            "videoProperties": "CC, HD 1080i, HDTV, New, Stereo"
        #        }
        #    ],
        #    "logoUrl": "https://fans.tmsimg.com/h5/NowShowing/28711/s28711_h5_aa.png",
        #    "name": "WCBSDT (WCBS-DT)"
        #}
        epg = []
        now = ('{0:.23s}{1:s}'.format(datetime.datetime.now().strftime('%Y-%m-%dT00:00:00'), '.155-07:00'))
        epg.extend(requests.post(BASE_API,
                                 data={'action': 'get_epgs', 'dma': self.primary_dma, 'start_time': now},
                                 cookies=self.build_cookies(),
                                 headers=self.build_header()).json())
        self.logger.info(f"Retrieved {len(epg)} channels from Locast")
        return epg


    def set_city(self):
        " Set dma and loc_name "
        #{
        #    "DMA": "501",
        #    "large_url": "https://s3.us-east-2.amazonaws.com/static.locastnet.org/cities/new-york.jpg",
        #    "name": "New York"
        #}
        #try:
        self.logger.debug(f"lat: {self.lat}, lon: {self.lon}")
        raw = requests.post(BASE_API, data={'action':'get_dma', 'lat':self.lat, 'lon':self.lon},
                            cookies=self.build_cookies(),
                            headers=self.build_header())
        self.logger.debug(f"get_dma: {raw.status_code}; {raw}, content: {raw.content}")
        city = raw.json()
        if city and ('DMA' not in city or 'name' not in city):
            self.logger.warning('DMA and name not found')
            return False
        else:
            self.logger.info(f"Running in {city['name']} (DMA: {city['DMA']})")
            self.primary_dma = city['DMA']
            self.loc_name = city['name']
            return True
        #except:
        #    self.logger.warning('setCity failed')


    def set_region(self):
        " Set lat/lon data "
        try:
            raw = requests.get(GEO_URL)
            self.logger.debug(f"set_region: {raw.status_code}, content: {raw.content}")
            geo_data = raw.json()
        except:
            geo_data = {'lat':0.0, 'lon':0.0}
        return float('{0:.7f}'.format(geo_data['lat'])), float('{0:.7f}'.format(geo_data['lon']))


    def resolve_url(self, station_id):
        " Translate station ID into streaming M3U8 URL "
        #{
        #    "active": true,
        #    "affiliate": "CBS",
        #    "affiliateName": "CBS",
        #    "callSign": "WCBS",
        #    "dma": 501,
        #    "id": 104,
        #    "logoUrl": "https://fans.tmsimg.com/h5/NowShowing/28711/s28711_h5_aa.png",
        #    "name": "WCBSDT (WCBS-DT)",
        #    "streamUrl": "https://www.kaltura.com/p/230/playManifest/entryId/1_qpkcj/uiConfId/40302/format/applehttp/protocol/https"
        #}
        resp = requests.post(BASE_API, data={'action':'get_station', 'station_id':str(station_id), 'lat':self.lat, 'lon':self.lon}, cookies=self.build_cookies(), headers=self.build_header())
        self.logger.debug(f"resolve_url: {resp.status_code}, {resp.content}")
        return resp.json()


class EPGGrabber(threading.Thread):
    "Class to grab EPG data from Locast and write it to an xmltv file"
    def __init__(self, lcst, outfile='test.xml'):
        " Create an EPGGrabber instance"
        threading.Thread.__init__(self)
        self.lcst = lcst
        self.outfile = outfile
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"Running EPGGrabber and writing to {self.outfile}")


    def write_xmltv_file(self):
        " Retrieve EPG data from Locast instance and write xmltv file "
        writer = xmltv.Writer()
        channels = []
        progs = []
        epg = self.lcst.get_epg()

        # Need an EPG to continue
        if not epg:
            return

        # Process the electronic program guide data
        for epg_chan in epg:
            # Create xmltv channel for each channel
            channel = {}
            channel['display-name'] = [(epg_chan['callSign'], u'en'), (epg_chan['name'], u'en')]
            channel['id'] = str(epg_chan['id']) + '.locast.org'
            channel['icon'] = [{'src': epg_chan['logoUrl']}]
            channels.append(channel)

            # Create xmltv programme for each listing
            for listing in epg_chan['listings']:
                prog = {'channel': str(listing.get('stationId')) + '.locast.org',
                        'new': listing.get('isNew', False),
                        'length': {'units': u'seconds', 'length': str(listing.get('duration', -1))},
                        'rating': [{'value': listing.get('rating', 'Unknown')}],
                        'start': time.strftime('%Y%m%d%H%M%S %Z', time.localtime(listing.get('startTime', 0) / 1000)),
                        'title': [(listing.get('title', ''), u'')]
                        }
                if 'genres' in listing:
                    prog['category'] = [(g, u'') for g in listing['genres'].split(',')]
                if 'topCast' in listing or 'directors' in listing:
                    prog['credits'] = {'director': listing.get('directors', '').split(','), 'actor': listing.get('topCast', '').split(',')}
                if 'releaseYear' in listing:
                    prog['date'] = str(listing['releaseYear'])
                if 'description' in listing:
                    prog['desc'] = [(listing['description'], u'')]
                if 'seasonNumber' in listing and 'episodeNumber' in listing:
                    episode = f"S{listing['seasonNumber']}E{listing['episodeNumber']}"
                    prog['episode-num'] = [(episode, u'common')]
                if 'episodeTitle' in listing:
                    prog['sub-title'] = [(listing.get('episodeTitle', ''), u'')]
                progs.append(prog)

        # Add channels and programs to xmltv writer
        for channel in channels:
            writer.addChannel(channel)
        for prog in progs:
            writer.addProgramme(prog)

        # Write the xmltv file
        writer.write(self.outfile, pretty_print=True)
        self.logger.info(f"Grabbed {len(channels)} channels and {len(progs)} programs for EPG")

        return


    def run(self):
        " Grab EPG data every 8 hours forever "
        while True:
            logging.info(f"Retrieving EPG data from Locast and writing to {self.outfile}")
            self.write_xmltv_file()
            os.system(f"cat {EPG_FILE} | socat - UNIX-CONNECT:{EPG_SOCK}")
            time.sleep(8 * 60 * 60)
            # Sleep 8 hours


################################################################################
# MAIN
LCST = Locast()
EPGGRABBER = EPGGrabber(LCST, EPG_FILE)

@APP.route('/locast')
def get_top_m3u8():
    " Top level M3U8 that has links back to us for each station "
    m3u = '#EXTM3U'

    stations = LCST.get_epg()
    logging.debug(f"get_top_m3u8->stations: {stations}")
    for station in stations:
        if not station['active']:
            continue

        m3u += f"\n#EXTINF:0,{station['callSign']}\n"
        m3u += f"{request.url_root}station/{station['id']}"

    logging.debug(f"get_m3U8: {m3u}")
    return Response(m3u, mimetype='application/x-mpegurl')


@APP.route('/station/<station_id>')
def play(station_id):
    " Grab station info and return stream URL "
    #{
    #    "active": true,
    #    "callSign": "WRC",
    #    "dma": 511,
    #    "id": 1014,
    #    "logo226Url": "https://s3.us-east-2.amazonaws.com/static.locastnet.org/roku/Washington/WRC.png",
    #    "logoUrl": "https://s3.us-east-2.amazonaws.com/static.locastnet.org/logo/Washington/WRC.png",
    #    "name": "WRCDT",
    #    "sequence": 10,
    #    "streamUrl": "https://cdn.locastnet.org/master/HXr63CNPzV2CjNfOlqKyIpZl==.m3u8?AdEt_4jYEZ-VNviP..."
    #}
    station_detail = LCST.resolve_url(station_id)
    m3u = '#EXTM3U'
    if not station_detail['active']:
        abort(404)
    m3u += f"\n#EXTINF\n{station_detail['streamUrl']}"

    logging.debug(f"play: {m3u}")
    return Response(m3u, mimetype='application/x-mpegurl')


if __name__ == '__main__':
    # 38.9885%2C-76.791
    time.sleep(180)
    EPGGRABBER.daemon = True
    EPGGRABBER.start()
    app.run(host='0.0.0.0', port=8080, debug=True)
