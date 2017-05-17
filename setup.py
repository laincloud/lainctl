# coding: utf-8
from setuptools import setup, find_packages
from lain_admin_cli import __version__

ENTRY_POINTS="""
[console_scripts]
lainctl = lain_admin_cli.cli:main
"""

requirements = [
    'argh==0.26.1',
    'argcomplete==0.9.0',
    'python-etcd==0.4.3',
    'requests==2.11.1'
]

setup(
    name="lain_admin_cli",
    version=__version__,
    packages=find_packages(),
    include_package_data=True,
    install_requires=requirements,
    entry_points=ENTRY_POINTS,
)
