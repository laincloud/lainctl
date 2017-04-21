# -*- coding: utf-8 -*-

import time
import etcd
import json
import hashlib
import requests
from os import environ
from argh.decorators import arg, expects_obj
from lain_admin_cli.helpers import TwoLevelCommandBase
from subprocess import check_output, call
from lain_admin_cli.helpers import info, error, sso_login


def get_etcd_client(etcd_authority):
    etcd_host_and_port = etcd_authority.split(":")
    if len(etcd_host_and_port) == 2:
        return etcd.Client(host=etcd_host_and_port[0], port=int(etcd_host_and_port[1]))
    elif len(etcd_host_and_port) == 1:
        return etcd.Client(host=etcd_host_and_port[0], port=4001)
    else:
        raise Exception("invalid ETCD_AUTHORITY : %s" % etcd_authority)


def get_console_domain():
    try:
        etcd_authority = environ.get("CONSOLE_ETCD_HOST", "etcd.lain:4001")
        client = get_etcd_client(etcd_authority)
        domain = client.read("/lain/config/domain").value
        try:
            main_domain = json.loads(client.read(
                "/lain/config/extra_domains").value)[0]
        except Exception as e:
            print("use %s as a default prefix of group name" % domain)
        else:
            domain = main_domain
        return domain
    except Exception:
        raise Exception("unable to get the console domain!")


class Auth(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.init, self.open, self.close]

    @classmethod
    def namespace(self):
        return "auth"

    @classmethod
    def help_message(self):
        return "lain auth operations"

    @classmethod
    @expects_obj
    @arg('-c', '--cid', default='3', help="Client id get from the sso system.")
    @arg('-s', '--secret', default='lain-cli_admin', help="Client secret get from the sso system.")
    @arg('-r', '--redirect_uri', default='https://example.com/', help="Redirect uri get from the sso system.")
    @arg('-u', '--sso_url', default='http://sso.lain.local', help="The sso_url need to be process")
    @arg('-a', '--check_all', default=False, help="Whether check all apps to create app groups in sso")
    def init(self, args):
        '''
        init the auth of lain, create groups in sso for lain apps
        '''
        login_success, token = sso_login(
            args.sso_url, args.cid, args.secret, args.redirect_uri)
        if login_success:
            add_sso_groups(args.sso_url, token, args.check_all)
        else:
            error("login failed.")
            exit(1)

    @classmethod
    @expects_obj
    @arg('-s', '--scope', default='all', choices=['console', 'all'])
    @arg('-t', '--type', default='lain-sso', help='The auth type for console')
    @arg('-u', '--url', default='http://sso.lain.local', help='the auth url for console')
    @arg('-r', '--realm', default='http://console.%s/api/v1/authorize/registry/' % get_console_domain(),
         help='the realm in which the registry server authenticates')
    @arg('-i', '--issuer', default='auth server', help='the name of registry token issuer')
    def open(self, args):
        '''
        open the auth of lain
        '''
        scope = args.scope
        info("Ready to open auth of %s:" % scope)
        if scope != 'all':
            open_ops[scope](args)
        else:
            for _, op in open_ops.iteritems():
                op(args)
        info("Done.")


    @classmethod
    @expects_obj
    @arg('-s', '--scope', default='all', choices=['registry', 'all'])
    def close(self, args):
        '''
        close the auth of lain
        '''
        scope = args.scope
        info("Ready to close auth of %s:" % scope)
        if scope != 'all':
            close_ops[scope]()
        else:
            for _, op in close_ops.iteritems():
                op()
        info("Done.")


# as in console/authorize/utils.py
def get_group_name_for_app(appname):
    appname_prefix = environ.get(
        "SSO_GROUP_NAME_PREFIX", "lainapp-%s" % get_console_domain())
    return (appname_prefix + "-" + appname).replace('.', '-')

# as in console/authorize/utils.py


def get_group_fullname_for_app(appname):
    group_fullname_prefix = environ.get(
        "SSO_GROUP_FULLNAME_PREFIX", "lain app in %s: " % get_console_domain())
    return "%s%s" % (group_fullname_prefix, appname)

# as in console/authorize/utils.py


def add_subgroup_for_admin(sso_url, access_token, appname, subname, role):
    group_name = get_group_name_for_app(appname)
    member_msg = {'role': role}
    headers = {"Content-Type": "application/json",
               "Accept": "application/json", 'Authorization': 'Bearer %s' % access_token}
    url = "%s/api/groups/%s/group-members/%s" % (sso_url, group_name, subname)
    return requests.request("PUT", url, headers=headers, json=member_msg, params=None)


def add_sso_groups(sso_url, token, check_all):
    if not check_all:
        appnames = ['console', 'registry', 'tinydns', 'webrouter', 'lvault']
        get_apps_success = True
    else:
        get_apps_success, appnames = get_console_apps(token)
    if not get_apps_success:
        return
    for app in appnames:
        try:
            group_name = get_group_name_for_app(app)
            group_fullname = get_group_fullname_for_app(app)
            group_msg = {'name': group_name, 'fullname': group_fullname}
            headers = {"Content-Type": "application/json",
                       "Accept": "application/json", 'Authorization': 'Bearer %s' % token}
            url = "%s/api/groups/" % sso_url
            req = requests.request(
                "POST", url, headers=headers, json=group_msg, verify=False)
            if req.status_code == 201:
                info("successfully create sso group for app %s" % app)
                resp = add_subgroup_for_admin(
                    sso_url, token, app, "lain", "admin")
                info('add subgroup lain, response code: %s' % resp.status_code)
            else:
                result = req.text
                print("create sso group for app %s wrong: %s" %
                      (app, result.encode('utf8')))
            time.sleep(3)
        except Exception as e:
            print("create sso group for app %s wrong: %s" % (app, e))


def get_console_apps(token):
    appnames = []
    try:
        url = "http://console.%s/api/v1/repos/" % get_console_domain()
        headers = {"Content-Type": "application/json", "access-token": token}
        req = requests.get(url, headers=headers)
        apps = json.loads(req.text)['repos']
        for app in apps:
            appnames.append(app['appname'])
        return True, appnames
    except Exception as e:
        print("Get console apps error: %s" % e)
        return False, appnames


def open_console_auth(args):
    info("opening console auth...")
    auth_setting = '{"type": "%s", "url": "%s"}' % (args.type, args.url)

    check_output(['etcdctl', 'set',
                  '/lain/config/auth/console',
                  auth_setting])


def close_console_auth():
    info("closing console auth...")
    call(['etcdctl', 'rm',
          '/lain/config/auth/console'],
         stderr=open('/dev/null', 'w'))


def open_registry_auth(args):
    info("opening registry auth...")
    auth_setting = '{"realm": "%s", "issuer": "%s", "service": "lain.local"}' % (
        args.realm, args.issuer)

    check_output(['etcdctl', 'set',
                  '/lain/config/auth/registry',
                  auth_setting])
    _restart_registry()


def close_registry_auth():
    info("closing registry auth...")
    call(['etcdctl', 'rm',
          '/lain/config/auth/registry'],
         stderr=open('/dev/null', 'w'))
    _restart_registry()


def _restart_registry():
    info("restarting registry...")
    try:
        container_ids = check_output(['docker', '-H', ':2376', 'ps', '-qf', 'name=registry.web.web'])
        for container_id in container_ids.splitlines():
            info("restarting registry container: %s" % container_id)
            check_output(['docker', '-H', ':2376', 'restart', container_id])
            time.sleep(3)
    except Exception as e:
        error("restart registry failed : %s, please try again or restart it manually." % str(e))


open_ops = {
    'console': open_console_auth,
    'registry': open_registry_auth
}

close_ops = {
    'console': close_console_auth,
    'registry': close_registry_auth
}
