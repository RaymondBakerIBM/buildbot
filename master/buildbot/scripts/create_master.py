# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

import os

import jinja2
from twisted.internet import defer
from twisted.python import util

from buildbot.config import master as config_master
from buildbot.master import BuildMaster
from buildbot.util import in_reactor


def makeBasedir(config):
    if os.path.exists(config['basedir']):
        if not config['quiet']:
            print("updating existing installation")
        return
    if not config['quiet']:
        print("mkdir", config['basedir'])
    os.mkdir(config['basedir'])


def makeTAC(config):
    # render buildbot_tac.tmpl using the config
    loader = jinja2.FileSystemLoader(os.path.dirname(__file__))
    env = jinja2.Environment(loader=loader, undefined=jinja2.StrictUndefined)
    env.filters['repr'] = repr
    tpl = env.get_template('buildbot_tac.tmpl')
    cxt = dict((k.replace('-', '_'), v) for k, v in config.items())
    contents = tpl.render(cxt)

    tacfile = os.path.join(config['basedir'], "buildbot.tac")
    if os.path.exists(tacfile):
        with open(tacfile, encoding='utf-8') as f:
            oldcontents = f.read()
        if oldcontents == contents:
            if not config['quiet']:
                print("buildbot.tac already exists and is correct")
            return
        if not config['quiet']:
            print("not touching existing buildbot.tac")
            print("creating buildbot.tac.new instead")
        tacfile += ".new"
    with open(tacfile, "w", encoding='utf-8') as f:
        f.write(contents)


def makeSampleConfig(config):
    source = util.sibpath(__file__, "sample.cfg")
    target = os.path.join(config['basedir'], "master.cfg.sample")
    if not config['quiet']:
        print(f"creating {target}")
    with open(source, encoding='utf-8') as f:
        config_sample = f.read()
    if config['db']:
        config_sample = config_sample.replace('sqlite:///state.sqlite', config['db'])
    with open(target, "w", encoding='utf-8') as f:
        f.write(config_sample)
    os.chmod(target, 0o600)


@defer.inlineCallbacks
def createDB(config):
    # create a master with the default configuration, but with db_url
    # overridden
    master_cfg = config_master.MasterConfig()
    master_cfg.db.db_url = config['db']
    master = BuildMaster(config['basedir'])
    master.config = master_cfg
    db = master.db
    yield db.setup(check_version=False, verbose=not config['quiet'])
    if not config['quiet']:
        print(f"creating database ({master_cfg.db.db_url})")
    yield db.model.upgrade()


@in_reactor
@defer.inlineCallbacks
def createMaster(config):
    makeBasedir(config)
    makeTAC(config)
    makeSampleConfig(config)
    yield createDB(config)

    if not config['quiet']:
        print(f"buildmaster configured in {config['basedir']}")

    return 0
