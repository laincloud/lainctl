# -*- coding: utf-8 -*-

import time
from argh.decorators import arg, expects_obj
from lain_admin_cli.helpers import TwoLevelCommandBase
from subprocess import check_output, call
from lain_admin_cli.helpers import info, error


AUTH_CHOICES = ['console', 'registry', 'all']


class Auth(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.open, self.close]

    @classmethod
    def namespace(self):
        return "auth"

    @classmethod
    def help_message(self):
        return "lain auth operations"

    @classmethod
    @expects_obj
    @arg('-s', '--scope', default='all', choices=AUTH_CHOICES)
    @arg('-t', '--type', default='lain-sso', help='The auth type for console')
    @arg('-u', '--url', default='https://sso.yxapp.in', help='the auth url for console')
    @arg('-r', '--realm', default='https://sso.yxapp.in/v2/token',
         help='the realm in which the registry server authenticates')
    @arg('-i', '--issuer', default='auth server', help='the name of registry token issuer')
    @arg('-d', '--domain', default='lain.bdp.cc', help='the domain where registry located')
    def open(self, args):
        '''
        open the auth of lain
        '''
        scope = args.scope
        info("ready to open auth of %s" % scope)
        if scope != 'all':
            open_ops[scope](args)
        else:
            for _, op in open_ops.iteritems():
                op(args)

    @classmethod
    @expects_obj
    @arg('-s', '--scope', default='all', choices=AUTH_CHOICES)
    def close(self, args):
        '''
        close the auth of lain
        '''
        scope = args.scope
        info("ready to close auth of %s" % scope)
        if scope != 'all':
            close_ops[scope]()
        else:
            for _, op in close_ops.iteritems():
                op()


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
    auth_setting = '{"realm": "%s", "issuer": "%s", "service": "%s"}' % (
        args.realm, args.issuer, args.domain)

    check_output(['etcdctl', 'set',
                  '/lain/config/auth/registry',
                  auth_setting])
    __restart_registry()


def close_registry_auth():
    info("closing registry auth...")
    call(['etcdctl', 'rm',
          '/lain/config/auth/registry'],
         stderr=open('/dev/null', 'w'))
    __restart_registry()


def __restart_registry():
    info("restarting registry...")
    try:

        container_id = check_output(['docker', '-H', ':2376', 'ps', '-qf', 'name=registry.web.web']).strip()

        info("container id of registry is : %s" % container_id)

        check_output(['docker', '-H', ':2376', 'stop', container_id])

        time.sleep(3)

        check_output(['docker', '-H', ':2376', 'start', container_id])

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
