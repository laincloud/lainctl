# -*- coding: utf-8 -*-

from argh.decorators import arg, expects_obj
from lain_admin_cli.helpers import (
    TwoLevelCommandBase, run_ansible_cmd
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
    @arg('-n', '--node', required=True, help='The nodename needing recovery')
    @arg('-t', '--target_app', required=True, help="The app needing recovery")
    @arg('-c', '--proc_name', required=True, help="The proc name needing recovery")
    @arg('-i', '--instance_number', required=True, help='the instance number of proc needing recovery')
    def recover(self, args):
        """
        network recover will fix docker network issues about container endpoint already exist;
        """
        run_recovernode_ansible(args)


def run_recovernode_ansible(args):
    envs = {
        'role': 'network-recover',
        'recover_node': args.node,
        'recover_app': args.target_app,
        'recover_proc': args.proc_name,
        'recover_instance_number': args.instance_number,
    }
    return run_ansible_cmd(args.playbooks, envs)
