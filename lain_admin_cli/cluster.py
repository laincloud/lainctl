# -*- coding: utf-8 -*-

from argh.decorators import arg
from lain_admin_cli.helpers import info, error, warn
from lain_admin_cli.helpers import TwoLevelCommandBase, run_ansible_cmd
from lain_admin_cli.utils.health import ClusterHealth


class Cluster(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.health]

    @classmethod
    def namespace(self):
        return "cluster"

    @classmethod
    def help_message(self):
        return "lain cluster maintainance"

    @classmethod
    def health(self):
        health = ClusterHealth()
        health.run()
