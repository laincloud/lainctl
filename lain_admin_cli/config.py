# -*- coding: utf-8 -*-

from argh.decorators import arg
from lain_admin_cli.helpers import TwoLevelCommandBase

class Config(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return []

    @classmethod
    def namespace(self):
        return "config"

    @classmethod
    def help_message(self):
        return "lain config operations"

    @classmethod
    @arg('item')
    def get(self, item):
        """
        get the configuration for given item
        """
        return "TODO"
        pass
