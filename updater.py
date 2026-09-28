"""Read-only checks of this project's public GitHub release metadata.

No CSE credentials, account names, paths, or reports are sent. Downloading and
installing is left to the user; the app only offers the validated release page.
"""
import json
import re
import urllib.error
import urllib.request

from build_info import VERSION, LANGUAGE, REPOSITORY

API_URL = 'https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
RELEASE_ROOT = 'https://github.com/'+REPOSITORY+'/releases/'
MAX_RESPONSE = 1024*1024


class UpdateError(Exception):
    pass


def version_tuple(value):
    if not isinstance(value, str) or len(value) > 32:
        raise UpdateError('更新版本号无效。')
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value)
    if not match:
        raise UpdateError('更新版本号无效。')
    return tuple(map(int, match.groups()))


def release_update(release, current=VERSION, language=LANGUAGE):
    if language not in {'zh', 'en'}:
        raise UpdateError('更新语言无效。')
    if not isinstance(release, dict):
        raise UpdateError('更新服务返回了无效内容。')
    if release.get('draft') is not False or release.get('prerelease') is not False:
        return {'available': False, 'version': current}
    tag = release.get('tag_name')
    latest = version_tuple(tag)
    if latest <= version_tuple(current):
        return {'available': False, 'version': current}
    page = RELEASE_ROOT+'tag/'+tag
    if release.get('html_url') != page:
        raise UpdateError('更新链接未通过验证。')
    name = 'CSE-Space-Helper-'+language+'.exe'
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise UpdateError('新版下载文件尚未准备完成。')
    matches = [item for item in assets if isinstance(item, dict) and item.get('name') == name]
    if len(matches) != 1:
        raise UpdateError('新版下载文件尚未准备完成。')
    asset = matches[0]
    url = RELEASE_ROOT+'download/'+tag+'/'+name
    if (asset.get('browser_download_url') != url or asset.get('state') != 'uploaded'
            or type(asset.get('size')) is not int or asset['size'] <= 0):
        raise UpdateError('新版下载文件未通过验证。')
    return {'available': True, 'version': tag.removeprefix('v'), 'page_url': page,
            'download_url': url, 'asset_name': name}


def check_release(current=VERSION, language=LANGUAGE, opener=None):
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(API_URL, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'CSE-Space-Helper/'+VERSION,
        'X-GitHub-Api-Version': '2026-03-10',
    })
    try:
        with opener(request, timeout=8) as response:
            if response.geturl() != API_URL:
                raise UpdateError('更新服务地址发生变化，请访问项目发布页。')
            content = response.read(MAX_RESPONSE+1)
        if len(content) > MAX_RESPONSE:
            raise UpdateError('更新服务返回内容过大。')
        release = json.loads(content.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in {403, 429}:
            raise UpdateError('更新服务暂时限制请求，请稍后重试。') from exc
        if exc.code == 404:
            raise UpdateError('暂时找不到公开发行版，请稍后重试。') from exc
        raise UpdateError('暂时无法连接更新服务，请稍后重试。') from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise UpdateError('暂时无法连接更新服务，请稍后重试。') from exc
    except (UnicodeError, ValueError) as exc:
        raise UpdateError('更新服务返回了无效内容。') from exc
    return release_update(release, current, language)
