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

from unittest import mock

from twisted.internet import defer
from twisted.trial import unittest

from buildbot import config
from buildbot.changes import pb
from buildbot.test.fake import fakemaster
from buildbot.test.reactor import TestReactorMixin
from buildbot.test.util import changesource
from buildbot.test.util import pbmanager


class TestPBChangeSource(
    changesource.ChangeSourceMixin, pbmanager.PBManagerMixin, TestReactorMixin, unittest.TestCase
):
    DEFAULT_CONFIG = {
        "port": '9999',
        "user": 'alice',
        "passwd": 'sekrit',
    }

    EXP_DEFAULT_REGISTRATION = ('9999', 'alice', 'sekrit')

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.setUpPBChangeSource()
        yield self.setUpChangeSource()

        self.master.pbmanager = self.pbmanager

    def test_registration_no_workerport(self):
        return self._test_registration(None, exp_ConfigErrors=True, user='alice', passwd='sekrit')

    def test_registration_global_workerport(self):
        return self._test_registration(self.EXP_DEFAULT_REGISTRATION, **self.DEFAULT_CONFIG)

    def test_registration_custom_port(self):
        return self._test_registration(
            ('8888', 'alice', 'sekrit'), user='alice', passwd='sekrit', port='8888'
        )

    def test_registration_no_userpass(self):
        return self._test_registration(('9939', 'change', 'changepw'), workerPort='9939')

    def test_registration_no_userpass_no_global(self):
        return self._test_registration(None, exp_ConfigErrors=True)

    @defer.inlineCallbacks
    def test_no_registration_if_master_already_claimed(self):
        # claim the CS on another master...
        yield self.setChangeSourceToMaster(self.OTHER_MASTER_ID)
        # and then use the same args as one of the above success cases,
        # but expect that it will NOT register
        yield self._test_registration(None, **self.DEFAULT_CONFIG)

    @defer.inlineCallbacks
    def test_registration_later_if_master_can_do_it(self):
        # get the changesource running but not active due to the other master
        yield self.setChangeSourceToMaster(self.OTHER_MASTER_ID)
        yield self.attachChangeSource(
            pb.PBChangeSource(name=self.DEFAULT_NAME, **self.DEFAULT_CONFIG)
        )
        self.startChangeSource()
        self.assertNotRegistered()

        # other master goes away
        yield self.setChangeSourceToMaster(None)

        # not quite enough time to cause it to activate
        self.reactor.advance(self.changesource.POLL_INTERVAL_SEC * 4 / 5)
        self.assertNotRegistered()

        # there we go!
        self.reactor.advance(self.changesource.POLL_INTERVAL_SEC * 2 / 5)
        self.assertRegistered(*self.EXP_DEFAULT_REGISTRATION)

    @defer.inlineCallbacks
    def _test_registration(
        self, exp_registration, exp_ConfigErrors=False, workerPort=None, **constr_kwargs
    ):
        cfg = mock.Mock()
        cfg.protocols = {'pb': {'port': workerPort}}
        self.attachChangeSource(pb.PBChangeSource(name=self.DEFAULT_NAME, **constr_kwargs))

        self.startChangeSource()
        if exp_ConfigErrors:
            # if it's not registered, it should raise a ConfigError.
            try:
                yield self.changesource.reconfigServiceWithBuildbotConfig(cfg)
            except config.ConfigErrors:
                pass
            else:
                self.fail("Expected ConfigErrors")
        else:
            yield self.changesource.reconfigServiceWithBuildbotConfig(cfg)

        if exp_registration:
            self.assertRegistered(*exp_registration)

        yield self.stopChangeSource()

        if exp_registration:
            self.assertUnregistered(*exp_registration)
        self.assertEqual(self.changesource.registration, None)

    @defer.inlineCallbacks
    def test_perspective(self):
        yield self.attachChangeSource(
            pb.PBChangeSource('alice', 'sekrit', name=self.DEFAULT_NAME, port='8888')
        )
        persp = self.changesource.getPerspective(mock.Mock(), 'alice')
        self.assertIsInstance(persp, pb.ChangePerspective)

    def test_describe(self):
        cs = pb.PBChangeSource()
        self.assertSubstring("PBChangeSource", cs.describe())

    def test_name(self):
        cs = pb.PBChangeSource(port=1234)
        self.assertEqual("PBChangeSource:1234", cs.name)

        cs = pb.PBChangeSource(port=1234, prefix="pre")
        self.assertEqual("PBChangeSource:pre:1234", cs.name)

        # explicit name:
        cs = pb.PBChangeSource(name="MyName")
        self.assertEqual("MyName", cs.name)

    def test_describe_prefix(self):
        cs = pb.PBChangeSource(prefix="xyz")
        self.assertSubstring("PBChangeSource", cs.describe())
        self.assertSubstring("xyz", cs.describe())

    def test_describe_int(self):
        cs = pb.PBChangeSource(port=9989)
        self.assertSubstring("PBChangeSource", cs.describe())

    @defer.inlineCallbacks
    def test_reconfigService_no_change(self):
        config = mock.Mock()
        yield self.attachChangeSource(pb.PBChangeSource(name=self.DEFAULT_NAME, port='9876'))

        self.startChangeSource()
        yield self.changesource.reconfigServiceWithBuildbotConfig(config)

        self.assertRegistered('9876', 'change', 'changepw')

        yield self.stopChangeSource()

        self.assertUnregistered('9876', 'change', 'changepw')

    @defer.inlineCallbacks
    def test_reconfigService_default_changed(self):
        config = mock.Mock()
        config.protocols = {'pb': {'port': '9876'}}
        yield self.attachChangeSource(pb.PBChangeSource(name=self.DEFAULT_NAME))

        self.startChangeSource()
        yield self.changesource.reconfigServiceWithBuildbotConfig(config)

        self.assertRegistered('9876', 'change', 'changepw')

        config.protocols = {'pb': {'port': '1234'}}

        yield self.changesource.reconfigServiceWithBuildbotConfig(config)

        self.assertUnregistered('9876', 'change', 'changepw')
        self.assertRegistered('1234', 'change', 'changepw')

        yield self.stopChangeSource()

        self.assertUnregistered('1234', 'change', 'changepw')

    @defer.inlineCallbacks
    def test_reconfigService_default_changed_but_inactive(self):
        """reconfig one that's not active on this master"""
        config = mock.Mock()
        config.protocols = {'pb': {'port': '9876'}}
        yield self.attachChangeSource(pb.PBChangeSource(name=self.DEFAULT_NAME))
        yield self.setChangeSourceToMaster(self.OTHER_MASTER_ID)

        self.startChangeSource()
        yield self.changesource.reconfigServiceWithBuildbotConfig(config)

        self.assertNotRegistered()

        config.protocols = {'pb': {'port': '1234'}}

        yield self.changesource.reconfigServiceWithBuildbotConfig(config)

        self.assertNotRegistered()

        yield self.stopChangeSource()

        self.assertNotRegistered()
        self.assertNotUnregistered()


class TestChangePerspective(TestReactorMixin, unittest.TestCase):
    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = yield fakemaster.make_master(self, wantDb=True, wantData=True)

    @defer.inlineCallbacks
    def test_addChange_noprefix(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({"who": 'bar', "files": ['a']})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': 'bar',
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': ['a'],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_codebase(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({"who": 'bar', "files": [], "codebase": 'cb'})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': 'bar',
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': 'cb',
                    'comments': None,
                    'files': [],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_prefix(self):
        cp = pb.ChangePerspective(self.master, 'xx/')
        yield cp.perspective_addChange({"who": 'bar', "files": ['xx/a', 'yy/b']})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': 'bar',
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': ['a'],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_sanitize_None(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({"project": None, "revlink": None, "repository": None})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': None,
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': [],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_when_None(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({"when": None})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': None,
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': [],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_files_tuple(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({"files": ('a', 'b')})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': None,
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': ['a', 'b'],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_unicode(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({
            "author": "\N{SNOWMAN}",
            "comments": "\N{SNOWMAN}",
            "files": ['\N{VERY MUCH GREATER-THAN}'],
        })

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': '\u2603',
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': '\u2603',
                    'files': ['\u22d9'],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_unicode_as_bytestring(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({
            "author": "\N{SNOWMAN}".encode(),
            "comments": "\N{SNOWMAN}".encode(),
            "files": ['\N{VERY MUCH GREATER-THAN}'.encode()],
        })

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': '\u2603',
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': '\u2603',
                    'files': ['\u22d9'],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_non_utf8_bytestring(self):
        cp = pb.ChangePerspective(self.master, None)
        bogus_utf8 = b'\xff\xff\xff\xff'
        replacement = bogus_utf8.decode('utf8', 'replace')
        yield cp.perspective_addChange({"author": bogus_utf8, "files": ['a']})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': replacement,
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': ['a'],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': None,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_addChange_old_param_names(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({"who": 'me', "when": 1234, "files": []})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': 'me',
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': [],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': None,
                    'when_timestamp': 1234,
                }
            ],
        )

    @defer.inlineCallbacks
    def test_createUserObject_git_src(self):
        cp = pb.ChangePerspective(self.master, None)
        yield cp.perspective_addChange({"who": 'c <h@c>', "src": 'git'})

        self.assertEqual(
            self.master.data.updates.changesAdded,
            [
                {
                    'author': 'c <h@c>',
                    'committer': None,
                    'branch': None,
                    'category': None,
                    'codebase': None,
                    'comments': None,
                    'files': [],
                    'project': '',
                    'properties': None,
                    'repository': '',
                    'revision': None,
                    'revlink': '',
                    'src': 'git',
                    'when_timestamp': None,
                }
            ],
        )
