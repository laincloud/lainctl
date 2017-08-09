# -*- coding: utf-8 -*-
import operator
import requests
import httplib
import json
import time

from datetime import datetime
from os import environ
from argh.decorators import arg
from argh import CommandError
from subprocess import check_output
from lain_admin_cli.helpers import (
    TwoLevelCommandBase, info, warn, error
)
from lain_admin_cli.utils.utils import regex_match

REPOS_URL_TEMPLATE = "http://%s/v2/_catalog"
TAGS_URL_TEMPLATE = "http://%s/v2/%s/tags/list"
MANIFEST_URL_TEMPLATE = "http://%s/v2/%s/manifests/%s"
HTTP_REGISTRY_HOST = 'http://%s'

REPOSITORIES = "repositories"
PREPARE = "prepare"
META = "meta"
RELEASE = "release"

registry_host = "registry.lain.local"
REGISTRY_FORMAT = "registry.%s"

TOKEN_CACHE = {}
REALM = "Bearer realm"
SCOPE = "scope"
SERVICE = "service"

TIME_OUT = environ.get('REGISTRY_TIMEOUT', 5)

DEFAULT_REMAIN_TIME = environ.get('REMAIN_TIME', 30 * 24 * 3600)


class Repo:

    def __init__(self, repo_name, tags):
        self.repo_name = repo_name
        self.tags = tags


class Image:

    def __init__(self, repo_name, tag, digest):
        self.repo_name = repo_name
        self.tag = tag
        self.digest = digest

    def __str__(self):
        return "{%s, %s, %s}" % (self.repo_name, self.tag, self.digest)


def _time_during(ts1, ts2, t):
    total_seconds = (datetime.fromtimestamp(ts1) -
                     datetime.fromtimestamp(ts2)).total_seconds()
    return total_seconds <= t


def _domain():
    try:
        return check_output(['etcdctl', 'get', '/lain/config/domain']).strip('\n')
    except Exception as e:
        error('Get lain domain failed! error:%s', str(e))


def _request_auth(session, method, url, auth_head, **kwargs):
    token = _token(auth_head)
    if token is None:
        return
    headers = {'Authorization': 'Bearer %s' % token}
    headers.update(kwargs)
    try:
        resp = session.request(method, url, headers=headers, timeout=TIME_OUT)
        if resp.status_code >= 300 or resp.status_code < 200:
            error('Requests url(%s) failed! error: registry server faltal error', url)
            return
        if resp.status_code == 401:
            token = _token(auth_head, expired=True)
            if token is None:
                return resp
            headers['Authorization'] = 'Bearer %s' % token
            resp = session.request(
                method, url, headers=headers, timeout=TIME_OUT)
        return resp
    except Exception as e:
        error('Requests url(%s) failed! error:%s', url, str(e))


def _request(session, method, url, **kwargs):
    try:
        resp = session.request(method, url, headers=kwargs, timeout=TIME_OUT)
        if resp.status_code == 401:
            auth_head = resp.headers['Www-Authenticate']
            resp_auth = _request_auth(
                session, method, url, auth_head, **kwargs)
            if resp_auth is not None:
                resp = resp_auth
        if resp is None:
            return
        if resp.status_code >= 300 or resp.status_code < 200:
            error('Requests url(%s) failed! error: registry server faltal error', url)
            return
        return resp
    except Exception as e:
        error('Requests url(%s) failed! error:%s', url, str(e))


def _token(auth_head, expired=False):
    token_url = _token_url(auth_head)
    token = TOKEN_CACHE.get(token_url)
    if not expired and token is not None:
        return token
    try:
        resp = requests.get(token_url)
        token = resp.json().get('token')
    except Exception as e:
        error('Fetch auth token failed ! error:%s', str(e))
        return token
    if token is not None:
        TOKEN_CACHE[token_url] = token
    return token


def _token_url(auth_head):
    auth_infos = auth_head.split(',')
    token_params = {}
    for info in auth_infos:
        k, v = info.split('=')
        token_params[k] = v.strip('"')
    token_url = "%s?service=%s&scope=%s" % (token_params[REALM], token_params[
                                            SERVICE], token_params[SCOPE])
    return token_url


def _registry_repos(session):
    url = REPOS_URL_TEMPLATE % registry_host
    repos = []
    try:
        while(True):
            resp = _request(session, 'GET', url)
            if resp is None:
                return[]
            repos.extend(resp.json().get(REPOSITORIES))
            link = resp.headers.get('Link', None)
            if link is None:
                break
            uri = regex_match(r'<(.*)>; rel="next"', link)
            url = (HTTP_REGISTRY_HOST % registry_host) + uri
        return repos
    except Exception as e:
        error('Fetch all repositories failed! error:%s', str(e))
    return repos


def _digest_from_tag(session, repo, tag):
    manifest_url = MANIFEST_URL_TEMPLATE % (registry_host, repo, tag)
    resp = _request(session, 'HEAD', manifest_url,
                    Accept="application/vnd.docker.distribution.manifest.v2+json")
    if resp is None:
        return ""
    return resp.headers.get('Docker-Content-Digest')


def _repo_images(session, repo):
    tags_url = TAGS_URL_TEMPLATE % (registry_host, repo)
    resp = _request(session, 'GET', tags_url)
    if resp is None:
        return []
    try:
        images = []
        tags = resp.json().get('tags')
        if not tags:
            return images
        for tag in tags:
            digest = _digest_from_tag(session, repo, tag)
            if digest != "":
                images.append(Image(repo, tag, digest))
        return images
    except Exception as e:
        error('Fetch repo(%s)\'s images failed! error:%s', repo, str(e))
    return []


def _image_timestamp(image):
    try:
        pos_timestamp = 1
        image_tag_split_len = 3
        tags_info = image.tag.split('-')
        if len(tags_info) != image_tag_split_len:
            return 0
        if image.tag.startswith(PREPARE):
            timestamp = int(tags_info[pos_timestamp + 1])
        else:
            timestamp = int(tags_info[pos_timestamp])
        return timestamp
    except Exception as e:
        warn('Fetch timestamp failed for image:%s!', image)
        return 0


def _delete_image(session, image):
    info("Deleting image: %s", image)
    manifest_url = MANIFEST_URL_TEMPLATE % (
        registry_host, image.repo_name, image.digest)
    resp = _request(session, 'DELETE', manifest_url)
    info("Delete image(%s) result:%s ", image, resp)


def ordered_images(session, repo):
    images = _repo_images(session, repo)
    times_images = {}
    for image in images:
        timestamp = _image_timestamp(image)
        times_images[str(timestamp) + image.tag] = image
    return sort_map_values(times_images)


def delete_image_tag(session, repo, tag):
    digest = _digest_from_tag(session, repo, tag)
    if digest == '':
        error('no such repo or repo has no such tag')
        return
    image = Image(repo, tag, digest)
    _delete_image(session, image)


def delete_repo(session, repo):
    info('start delete repo:%s!', repo)
    images = _repo_images(session, repo)
    for image in images:
        _delete_image(session, image)
    info('delete repo:%s over!', repo)


def clear_expired_repo(session, repo, repo_remain, time_remain):
    info('----------------------------')
    info('Start clean registry repo %s', repo)
    classified_images = {
        META: {}, RELEASE: {}, PREPARE: {}
    }
    now = time.time()
    try:
        images = _repo_images(session, repo)
        if len(images) <= repo_remain:
            return
        for image in images:
            try:
                image_type = image.tag.split('-')[0]
            except Exception as e:
                warn('Strange image :%s', image.tag)
                continue
            timestamp = _image_timestamp(image)
            if timestamp == 0:
                if image.tag.find('-config-') > 0:
                    info("specific config image: %s", image)
                    _delete_image(session, image)
                continue
            if(_time_during(now, timestamp, time_remain)):
                continue

            target_type_images = classified_images.get(image_type, None)
            if target_type_images is None:
                warn('Strange image type:%s', image.tag)
                continue

            target_type_images[timestamp] = image

        for _, image_map in classified_images.items():
            sorted_images = sort_map_values(image_map)
            for image in sorted_images[repo_remain:]:
                _delete_image(session, image)
    except Exception as e:
        error('Clean registry failed! error:%s', str(e))
    finally:
        info('Clean registry repo %s over', repo)


def clear_all_expired_repos(session, repo_remain, time_remain):
    info('Start clean registry')
    info('============================')
    repos = _registry_repos(session)
    if not isinstance(repos, list) or len(repos) == 0:
        return
    for repo in repos:
        clear_expired_repo(session, repo, repo_remain, time_remain)
    info('============================')
    info('Clean registry over')


def sort_map_values(origin_map):
    return [item[1] for item in sorted(origin_map.items(),
                                       key=operator.itemgetter(0), reverse=True)]


class Registry(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.list, self.delete, self.clean]

    @classmethod
    def namespace(self):
        return "registry"

    @classmethod
    def help_message(self):
        return "lain registry operations"

    @classmethod
    @arg('-t', '--target', required=False, help="target repository in registry")
    @arg('-s', '--sort', required=False, help="return results in order")
    def list(self, target="all", sort=False):
        session = requests.Session()
        self._update_domain()
        if target == "all":
            repos = _registry_repos(session)
            for repo in repos:
                info(repo)
        else:
            if sort:
                images = ordered_images(session, target)
            else:
                images = _repo_images(session, target)
            for image in images:
                info('%s', image)

    @classmethod
    @arg('-r', '--repo', required=True, help="repository in registry")
    @arg('-t', '--tag', required=False, help="image tag in registry")
    def delete(self, repo='', tag=''):
        session = requests.Session()
        self._update_domain()
        if tag != '':
            delete_image_tag(session, repo, tag)
        else:
            delete_repo(session, repo)

    @classmethod
    @arg('-t', '--target', required=False, help="clean target repository in registry")
    @arg('-n', '--num', required=False, help="repository's remained quantity of images in registry(must bigger than 0)")
    @arg('-d', '--time', required=False, help="repository's remained time(seconds) of images in registry(must bigger than 0)")
    def clean(self, num=20, time=DEFAULT_REMAIN_TIME, target="all"):
        session = requests.Session()
        self._update_domain()
        if num < 1:
            raise CommandError("num must bigger than 0")
        if time < 1:
            raise CommandError("time must bigger than 0")
        if target == "all":
            clear_all_expired_repos(session, num, time)
        else:
            clear_expired_repo(session, target, num, time)

    @classmethod
    def _update_domain(self):
        domain = _domain()
        if domain is not None:
            global registry_host
            registry_host = REGISTRY_FORMAT % _domain()
