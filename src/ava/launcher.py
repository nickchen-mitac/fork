# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals


import os
import sys
import logging
import shutil
import multiprocessing

from ava import APP_NAME
from ava.core.agent import start_agent
from ava.util import base_path

#makes multiprocessing work when in freeze mode.
multiprocessing.freeze_support()

try:  # Python 2.7+
    from logging import NullHandler
except ImportError:
    class NullHandler(logging.Handler):
        def emit(self, record):
            pass

_logger = logging.getLogger(__name__)

_logger.addHandler(NullHandler())


def _posixify(name):
    return '-'.join(name.split()).lower()


def get_app_dir(app_name=APP_NAME, roaming=True, force_posix=False):
    if sys.platform.startswith(b'win'):
        key = roaming and 'APPDATA' or 'LOCALAPPDATA'
        folder = os.environ.get(key)
        if folder is None:
            folder = os.path.expanduser('~')
        return os.path.join(folder, app_name)
    if force_posix:
        return os.path.join(os.path.expanduser('~/.' + _posixify(app_name)))
    if sys.platform.startswith(b'darwin'):
        return os.path.join(os.path.expanduser(
            '~/Library/Application Support'), app_name)
    return os.path.join(
        os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')),
        _posixify(app_name))


def init_app_dir(folder=None):
    """
    Constructs the skeleton of directories if it not there already.
    :return:
    """
    if folder is None:
        folder = get_app_dir()

    _logger.debug("Initializing app folder...")
    if os.path.exists(folder):
        _logger.info("App folder '%s' exists, abort initialization." %
                     folder)
        return

    os.makedirs(folder)

    src_dir = os.path.join(base_path(), 'pod')
    # copy files from base_dir to user_dir
    subdirs = os.listdir(src_dir)
    # ignore_pattern = shutil.ignore_patterns("__init__.py")
    for d in subdirs:
        src_path = os.path.join(src_dir, d)
        dst_path = os.path.join(folder, d)
        if os.path.isdir(src_path):
            shutil.copytree(src_path, dst_path)
        else:
            shutil.copy2(src_path, dst_path)


def launch():
    app_dir = get_app_dir()
    _logger.info("App dir: %s" % app_dir)

    if not os.path.exists(app_dir):
        init_app_dir(app_dir)

    if not os.path.isdir(app_dir):
        _logger.error("Invalid app folder: %s" % app_dir)
        sys.exit(-1)

    from ava.cmds.cli import cli

    return cli(auto_envvar_prefix=b'AVA')

if __name__ == '__main__':
    launch()

