# -*- coding: utf-8 -*-

import sys
from argh.decorators import arg, expects_obj
from lain_admin_cli.helpers import (
    TwoLevelCommandBase, run_ansible_cmd, warn, error
)


class Network(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.recover]

    @classmethod
    def namespace(self):
        return "network"

    @classmethod
    def help_message(self):
        return "lain network operations"

    @classmethod
    @expects_obj
    @arg('-p', '--playbooks', required=True)
    @arg('-n', '--node', required=True, help='Target node name, the node name needing recovery')
    @arg('-t', '--target_app', required=True, help="Target app name, the appname needing recovery")
    @arg('-P', '--proc_name', required=True, help="Proc name, the procname needing recovery; \
                            when recovering portal, the procname is normally like: portal-{service_name}")
    @arg('-i', '--instance_number', help='Instance number, defined when the recover container is not portal')
    @arg('-c', '--client_app', help='Client appname of the recover service, defined when the recover container is portal')
    def recover(self, args):
        """
        network recover will fix docker network issues about container endpoint already exist;
        """
        if not args.instance_number and not args.client_app:
            error('need defining instance number with -i, or client app with -c')
            sys.exit(1)

        if args.instance_number and args.client_app:
            warn('defined both instance number and client app, we donot know which container you want to recover')
            sys.exit(1)

        run_recovernode_ansible(args)


def run_recovernode_ansible(args):
    envs = {
        'role': 'network-recover',
        'recover_node': args.node,
        'recover_app': args.target_app,
        'recover_proc': args.proc_name,
        'recover_instance_number': args.instance_number if args.instance_number else 0,
        'recover_client_app': args.client_app if args.client_app else '',
    }
    return run_ansible_cmd(args.playbooks, envs)
