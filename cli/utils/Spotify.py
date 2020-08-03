import os
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cli.utils.constants import *
from cli.utils.exceptions import *


def _read_json(file_path):
    if not os.path.exists(file_path):
        return {}
    with open(file_path) as f:
        data = json.load(f)
    return data

def get_credentials():
    """Read locally stored credentials file for API authorization."""
    return _read_json(CREDS_PATH)

def get_config():
    return _read_json(CONFIG_PATH)

def update_config(update_dict):
    config = get_config()
    config.update(update_dict)
    with open(CONFIG_PATH, 'w+') as f:
        json.dump(config, f)
    return


def refresh(auth_code=None):
    """Refresh Spotify access token.

    Optional arguments:
    auth_code -- if supplied, pulls new access and refresh tokens.
    """
    post_data = {}
    if auth_code:
        post_data = {'auth_code': auth_code}
    else:
        refresh_token = get_credentials().get('refresh_token')
        if refresh_token:
            post_data = {'refresh_token': refresh_token}
        else:
            raise AuthorizationError

    req = Request(
        REFRESH_URI,
        json.dumps(post_data).encode('ascii'),
        DEFAULT_HEADERS,
        method='POST'
    )
    with urlopen(req) as refresh_res:
        refresh_str = refresh_res.read()
        if type(refresh_str) == bytes:
            refresh_str = refresh_str.decode('utf-8')

        refresh_data = json.loads(refresh_str)
        if 'access_token' not in refresh_data.keys():
            raise AuthorizationError

    creds = get_credentials()
    creds.update(refresh_data)
    if auth_code:
        creds['auth_code'] = auth_code

    with open(CREDS_PATH, 'w+') as f:
        json.dump(creds, f)

    return refresh_data


def _handle_request(endpoint, method='GET', data=None, headers={}, ignore_errs=[], handle_errs={}):
    if endpoint.startswith('/'):
        endpoint = endpoint[1:]

    # allow full URL to be passed
    if endpoint.startswith(API_URL):
        url = endpoint
    else:
        url = API_URL + endpoint

    if data:
        data = json.dumps(data).encode('ascii')
        
    headers.update(DEFAULT_HEADERS)
    req = Request(url, data, headers, method=method)
    try:
        with urlopen(req) as res:
            if res.status == 200:
                res_str = res.read()
                if type(res_str) == bytes:
                    res_str = res_str.decode('utf-8')

                if len(res_str) == 0:
                    return {}
                else:
                    return json.loads(res_str)

            elif res.status == 204:
                return {}

    except HTTPError as e:
        if e.status in ignore_errs:
            pass
        elif handle_errs.get(e.status):
            _exc = handle_errs[e.status]
            if type(_exc) not in [list, tuple]:
                raise _exc
            else:
                exc, kwargs = _exc
                raise exc(**kwargs)

        elif e.status == 401:
            raise TokenExpired
        else:
            raise SpotifyAPIError(message=e.msg, status=e.status)


def request(endpoint, *args, **kwargs):
    """Request wrapper for Spotify API endpoints. Handles errors, authorization,
    and refreshing access if needed.
    """
    access_token = get_credentials().get('access_token')
    if not access_token:
        raise AuthorizationError

    kwargs['headers'] = kwargs.get('headers', {})
    kwargs['headers']['Authorization'] = 'Bearer ' + access_token
    try:
        res_data = _handle_request(endpoint, *args, **kwargs)
    except TokenExpired:
        # refresh & retry if expired
        access_token = refresh()['access_token']
        kwargs['headers']['Authorization'] = 'Bearer ' + access_token
        res_data = _handle_request(endpoint, *args, **kwargs)

    return res_data


def multirequest(requests_arr=[], wait=False):
    from concurrent.futures import ThreadPoolExecutor
    executor = ThreadPoolExecutor(max_workers=5)
    futures = [
        executor.submit(request, **req)
        for req in requests_arr
    ]
    executor.shutdown(wait=wait)
    return futures


class Pager:
    def __init__(self, endpoint, limit=20, offset=0, params={}, content_callback=None, *args, **kwargs):
        limit = min(50, limit)
        self.endpoint = endpoint
        self._content_callback = content_callback
        self._args = args
        self._kwargs = kwargs
        self._endpoint_formatted = endpoint + '?limit={}&offset={}'.format(limit, offset)
        if params:
            for k, v in params.items():
                self._endpoint_formatted += '&{}={}'.format(k, v)

        self.content = request(self._endpoint_formatted, *args, **kwargs)
        if self._content_callback:
            self.content = self._content_callback(self.content)
        self._update_from_content()
        return

    def _update_from_content(self):
        self.items = self.content['items']
        self.next_url = self.content['next']
        self.previous_url = self.content.get('previous')
        self.total = self.content.get('total')
        self.limit = self.content['limit']
        self.offset = self.content.get('offset')    # index of first result
        return

    def next(self):
        if not self.items or not self.next_url:
            raise PagerLimitReached

        self.content = request(self.next_url, *self._args, **self._kwargs)
        if self._content_callback:
            self.content = self._content_callback(self.content)
        self._update_from_content()
        return

    def previous(self):
        if not self.previous_url:
            raise PagerPreviousUnavailable

        self.content = request(self.previous_url, *self._args, **self._kwargs)
        self._update_from_content()
        return
