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
from buildbot.changes import changes
from buildbot.changes import filter
from buildbot.process import properties
from buildbot.process.properties import Interpolate
from buildbot.schedulers import base
from buildbot.test import fakedb
from buildbot.test.reactor import TestReactorMixin
from buildbot.test.util import scheduler
from buildbot.test.util.config import ConfigErrorsMixin
from buildbot.test.util.warnings import assertProducesWarnings
from buildbot.warnings import DeprecatedApiWarning


class TestReconfigurableBaseScheduler(
    scheduler.SchedulerMixin, ConfigErrorsMixin, TestReactorMixin, unittest.TestCase
):
    OBJECTID = 19
    SCHEDULERID = 9
    exp_bsid_brids = (123, {'b': 456})

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        yield self.setUpScheduler()

        @defer.inlineCallbacks
        def stop_master():
            if self.master.running:
                yield self.master.stopService()

        self.addCleanup(stop_master)

    @defer.inlineCallbacks
    def makeScheduler(self, name='testsched', builderNames=None, properties=None, codebases=None):
        if builderNames is None:
            builderNames = ['a', 'b']
        if properties is None:
            properties = {}
        if codebases is None:
            codebases = {'': {}}

        if isinstance(builderNames, list):
            dbBuilder = []
            builderid = 0
            for builderName in builderNames:
                if isinstance(builderName, str):
                    builderid += 1
                    dbBuilder.append(fakedb.Builder(id=builderid, name=builderName))

            yield self.master.db.insert_test_data(dbBuilder)

        sched = yield self.attachScheduler(
            base.ReconfigurableBaseScheduler(
                name=name, builderNames=builderNames, properties=properties, codebases=codebases
            ),
            self.OBJECTID,
            self.SCHEDULERID,
        )
        self.master.data.updates.addBuildset = mock.Mock(
            name='data.addBuildset',
            side_effect=lambda *args, **kwargs: defer.succeed(self.exp_bsid_brids),
        )

        return sched

    # tests

    @defer.inlineCallbacks
    def test_constructor_builderNames(self):
        with self.assertRaises(config.ConfigErrors):
            yield self.makeScheduler(builderNames='xxx')

    @defer.inlineCallbacks
    def test_constructor_builderNames_unicode(self):
        yield self.makeScheduler(builderNames=['a'])

    @defer.inlineCallbacks
    def test_constructor_builderNames_renderable(self):
        @properties.renderer
        def names(props):
            return ['a']

        yield self.makeScheduler(builderNames=names)

    @defer.inlineCallbacks
    def test_integer_builderNames(self):
        with self.assertRaisesConfigError(
            "builderNames argument to a scheduler must be a list of Builder names"
        ):
            yield self.makeScheduler(builderNames=1234)

    @defer.inlineCallbacks
    def test_listofints_builderNames(self):
        with self.assertRaisesConfigError(
            "builderNames argument to a scheduler must be a list of Builder names"
        ):
            yield self.makeScheduler(builderNames=[1234])

    @defer.inlineCallbacks
    def test_listofmixed_builderNames(self):
        with self.assertRaisesConfigError(
            "builderNames argument to a scheduler must be a list of Builder names"
        ):
            yield self.makeScheduler(builderNames=['test', 1234])

    @defer.inlineCallbacks
    def test_constructor_codebases_valid(self):
        codebases = {"codebase1": {"repository": "", "branch": "", "revision": ""}}
        yield self.makeScheduler(codebases=codebases)

    @defer.inlineCallbacks
    def test_constructor_codebases_valid_list(self):
        codebases = ['codebase1']
        yield self.makeScheduler(codebases=codebases)

    @defer.inlineCallbacks
    def test_constructor_codebases_invalid(self):
        # scheduler only accepts codebases with at least repository set
        codebases = {"codebase1": {"dictionary": "", "that": "", "fails": ""}}
        with self.assertRaises(config.ConfigErrors):
            yield self.makeScheduler(codebases=codebases)

    @defer.inlineCallbacks
    def test_getCodebaseDict(self):
        sched = yield self.makeScheduler(codebases={'lib': {'repository': 'librepo'}})
        yield self.master.startService()
        cbd = yield sched.getCodebaseDict('lib')
        self.assertEqual(cbd, {'repository': 'librepo'})

    @defer.inlineCallbacks
    def test_getCodebaseDict_constructedFromList(self):
        sched = yield self.makeScheduler(codebases=['lib', 'lib2'])
        yield self.master.startService()
        cbd = yield sched.getCodebaseDict('lib')
        self.assertEqual(cbd, {})

    @defer.inlineCallbacks
    def test_getCodebaseDict_not_found(self):
        sched = yield self.makeScheduler(codebases={'lib': {'repository': 'librepo'}})
        yield self.master.startService()
        with self.assertRaises(KeyError):
            yield sched.getCodebaseDict('app')

    @defer.inlineCallbacks
    def test_listBuilderNames(self):
        sched = yield self.makeScheduler(builderNames=['x', 'y'])
        yield self.master.startService()
        self.assertEqual(sched.listBuilderNames(), ['x', 'y'])

    @defer.inlineCallbacks
    def test_startConsumingChanges_fileIsImportant_check(self):
        sched = yield self.makeScheduler()
        try:
            yield sched.startConsumingChanges(fileIsImportant="maybe")
        except AssertionError:
            pass
        else:
            self.fail("didn't assert")

    @defer.inlineCallbacks
    def test_enabled_callback(self):
        sched = yield self.makeScheduler()
        expectedValue = not sched.enabled
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, expectedValue)
        expectedValue = not sched.enabled
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, expectedValue)

    @defer.inlineCallbacks
    def do_test_change_consumption(self, kwargs, expected_result, change_kwargs=None):
        if change_kwargs is None:
            change_kwargs = {}

        # (expected_result should be True (important), False (unimportant), or
        # None (ignore the change))
        sched = yield self.makeScheduler()
        sched.startService()
        self.addCleanup(sched.stopService)

        # set up a change message, a changedict, a change, and convince
        # getChange and fromChdict to convert one to the other
        msg = {"changeid": 12934}

        chdict = {"changeid": 12934, "is_chdict": True}

        def getChange(changeid):
            assert changeid == 12934
            return defer.succeed(chdict)

        self.master.db.changes.getChange = getChange

        change = self.makeFakeChange(**change_kwargs)
        change.number = 12934

        def fromChdict(cls, master, chdict):
            assert chdict['changeid'] == 12934 and chdict['is_chdict']
            return defer.succeed(change)

        self.patch(changes.Change, 'fromChdict', classmethod(fromChdict))

        change_received = [None]

        def gotChange(got_change, got_important):
            # check that we got the expected change object
            self.assertIdentical(got_change, change)
            change_received[0] = got_important
            return defer.succeed(None)

        sched.gotChange = gotChange

        yield sched.startConsumingChanges(**kwargs)

        # check that it registered callbacks
        self.assertEqual(len(self.master.mq.qrefs), 2)

        qref = self.master.mq.qrefs[1]
        self.assertEqual(qref.filter, ('changes', None, 'new'))

        # invoke the callback with the change, and check the result
        qref.callback('change.12934.new', msg)
        self.assertEqual(change_received[0], expected_result)

    def test_change_consumption_defaults(self):
        # all changes are important by default
        return self.do_test_change_consumption({}, True)

    def test_change_consumption_fileIsImportant_True(self):
        return self.do_test_change_consumption({"fileIsImportant": lambda c: True}, True)

    def test_change_consumption_fileIsImportant_False(self):
        return self.do_test_change_consumption({"fileIsImportant": lambda c: False}, False)

    @defer.inlineCallbacks
    def test_change_consumption_fileIsImportant_exception(self):
        yield self.do_test_change_consumption({"fileIsImportant": lambda c: 1 / 0}, None)

        self.assertEqual(1, len(self.flushLoggedErrors(ZeroDivisionError)))

    def test_change_consumption_change_filter_True(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: True
        return self.do_test_change_consumption({"change_filter": cf}, True)

    def test_change_consumption_change_filter_False(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: False
        return self.do_test_change_consumption({"change_filter": cf}, None)

    def test_change_consumption_change_filter_gerrit_ref_updates(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: False
        return self.do_test_change_consumption(
            {'change_filter': cf},
            None,
            change_kwargs={'category': 'ref-updated', 'branch': 'master'},
        )

    def test_change_consumption_change_filter_gerrit_ref_updates_with_refs(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: False
        return self.do_test_change_consumption(
            {'change_filter': cf},
            None,
            change_kwargs={'category': 'ref-updated', 'branch': 'refs/changes/123'},
        )

    def test_change_consumption_change_filter_gerrit_filters_branch_new(self):
        cf = filter.ChangeFilter(branch='master')
        return self.do_test_change_consumption(
            {'change_filter': cf},
            True,
            change_kwargs={'category': 'ref-updated', 'branch': 'master'},
        )

    def test_change_consumption_change_filter_gerrit_filters_branch_new_not_match(self):
        cf = filter.ChangeFilter(branch='other')
        return self.do_test_change_consumption(
            {'change_filter': cf},
            None,
            change_kwargs={'category': 'ref-updated', 'branch': 'master'},
        )

    def test_change_consumption_fileIsImportant_False_onlyImportant(self):
        return self.do_test_change_consumption(
            {"fileIsImportant": lambda c: False, "onlyImportant": True}, None
        )

    def test_change_consumption_fileIsImportant_True_onlyImportant(self):
        return self.do_test_change_consumption(
            {"fileIsImportant": lambda c: True, "onlyImportant": True}, True
        )

    @defer.inlineCallbacks
    def test_activation(self):
        sched = yield self.makeScheduler(name='n', builderNames=['a'])
        sched.activate = mock.Mock(return_value=defer.succeed(None))
        sched.deactivate = mock.Mock(return_value=defer.succeed(None))

        # set the schedulerid, and claim the scheduler on another master
        yield self.setSchedulerToMaster(self.OTHER_MASTER_ID)

        yield self.master.startService()
        self.reactor.advance(sched.POLL_INTERVAL_SEC / 2)
        self.reactor.advance(sched.POLL_INTERVAL_SEC / 5)
        self.reactor.advance(sched.POLL_INTERVAL_SEC / 5)
        self.assertFalse(sched.activate.called)
        self.assertFalse(sched.deactivate.called)
        self.assertFalse(sched.isActive())
        # objectid is attached by the test helper
        self.assertEqual(sched.serviceid, self.SCHEDULERID)

        # clear that masterid
        yield sched.stopService()
        yield self.setSchedulerToMaster(None)
        yield sched.startService()
        self.reactor.advance(sched.POLL_INTERVAL_SEC)
        self.assertTrue(sched.activate.called)
        self.assertFalse(sched.deactivate.called)
        self.assertTrue(sched.isActive())

        # stop the service and see that deactivate is called
        yield self.master.stopService()
        self.assertTrue(sched.activate.called)
        self.assertTrue(sched.deactivate.called)
        self.assertFalse(sched.isActive())

    @defer.inlineCallbacks
    def test_activation_claim_raises(self):
        sched = yield self.makeScheduler(name='n', builderNames=['a'])

        # set the schedulerid, and claim the scheduler on another master
        yield self.setSchedulerToMaster(self.OTHER_MASTER_ID)

        sched.startService()
        self.assertFalse(sched.isActive())

    @defer.inlineCallbacks
    def test_activation_activate_fails(self):
        sched = yield self.makeScheduler(name='n', builderNames=['a'])

        def activate():
            raise RuntimeError('oh noes')

        sched.activate = activate

        yield self.master.startService()
        self.assertEqual(1, len(self.flushLoggedErrors(RuntimeError)))

    @defer.inlineCallbacks
    def do_addBuildsetForSourceStampsWithDefaults(self, codebases, sourcestamps, exp_sourcestamps):
        sched = yield self.makeScheduler(name='n', builderNames=['b'], codebases=codebases)
        yield self.master.startService()

        bsid, brids = yield sched.addBuildsetForSourceStampsWithDefaults(
            reason='power', sourcestamps=sourcestamps, waited_for=False
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        call = self.master.data.updates.addBuildset.mock_calls[0]

        def sourceStampKey(sourceStamp):
            repository = sourceStamp.get('repository', '')
            if repository is None:
                repository = ''
            branch = sourceStamp.get('branch', '') if not None else ''
            if branch is None:
                branch = ''
            return (repository, branch)

        self.assertEqual(
            sorted(call[2]['sourcestamps'], key=sourceStampKey),
            sorted(exp_sourcestamps, key=sourceStampKey),
        )

    def test_addBuildsetForSourceStampsWithDefaults(self):
        codebases = {
            'cbA': {"repository": 'svn://A..', "branch": 'stable', "revision": '13579'},
            'cbB': {"repository": 'svn://B..', "branch": 'stable', "revision": '24680'},
        }
        sourcestamps = [
            {'codebase': 'cbA', 'branch': 'AA'},
            {'codebase': 'cbB', 'revision': 'BB'},
        ]
        exp_sourcestamps = [
            {
                'repository': 'svn://B..',
                'branch': 'stable',
                'revision': 'BB',
                'codebase': 'cbB',
                'project': '',
            },
            {
                'repository': 'svn://A..',
                'branch': 'AA',
                'project': '',
                'revision': '13579',
                'codebase': 'cbA',
            },
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults(
            codebases, sourcestamps, exp_sourcestamps
        )

    def test_addBuildsetForSourceStampsWithDefaults_fill_in_codebases(self):
        codebases = {
            'cbA': {"repository": 'svn://A..', "branch": 'stable', "revision": '13579'},
            'cbB': {"repository": 'svn://B..', "branch": 'stable', "revision": '24680'},
        }
        sourcestamps = [
            {'codebase': 'cbA', 'branch': 'AA'},
        ]
        exp_sourcestamps = [
            {
                'repository': 'svn://B..',
                'branch': 'stable',
                'revision': '24680',
                'codebase': 'cbB',
                'project': '',
            },
            {
                'repository': 'svn://A..',
                'branch': 'AA',
                'project': '',
                'revision': '13579',
                'codebase': 'cbA',
            },
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults(
            codebases, sourcestamps, exp_sourcestamps
        )

    def test_addBuildsetForSourceStampsWithDefaults_no_repository(self):
        exp_sourcestamps = [
            {'repository': '', 'branch': None, 'revision': None, 'codebase': '', 'project': ''},
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults({'': {}}, [], exp_sourcestamps)

    def test_addBuildsetForSourceStamps_unknown_codbases(self):
        codebases = {}
        sourcestamps = [
            {'codebase': 'cbA', 'branch': 'AA'},
            {'codebase': 'cbB', 'revision': 'BB'},
        ]
        exp_sourcestamps = [
            {'branch': None, 'revision': 'BB', 'codebase': 'cbB', 'project': '', 'repository': ''},
            {'branch': 'AA', 'revision': None, 'codebase': 'cbA', 'project': '', 'repository': ''},
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults(
            codebases, sourcestamps, exp_sourcestamps
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_one_change(self):
        sched = yield self.makeScheduler(name='n', builderNames=['b'])
        yield self.master.startService()

        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=234),
            fakedb.Change(changeid=13, sourcestampid=234),
        ])
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='power', waited_for=False, changeids=[13]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            reason='power',
            scheduler='n',
            sourcestamps=[234],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_properties(self):
        sched = yield self.makeScheduler(name='n', builderNames=['c'])
        yield self.master.startService()

        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=234),
            fakedb.Change(changeid=14, sourcestampid=234),
        ])
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='downstream', waited_for=False, changeids=[14]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            reason='downstream',
            scheduler='n',
            sourcestamps=[234],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_properties_with_virtual_builders(self):
        sched = yield self.makeScheduler(
            name='n',
            builderNames=['c'],
            properties={'virtual_builder_name': Interpolate("myproject-%(src::branch)s")},
        )
        yield self.master.startService()

        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=234, branch='dev1', project="linux"),
            fakedb.Change(changeid=14, sourcestampid=234, branch="dev1"),
        ])
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='downstream', waited_for=False, changeids=[14]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={
                'virtual_builder_name': ("myproject-dev1", "Scheduler"),
                'scheduler': ('n', 'Scheduler'),
            },
            reason='downstream',
            scheduler='n',
            sourcestamps=[234],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_multiple_changes_same_codebase(self):
        # This is a test for backwards compatibility
        # Changes from different repositories come together in one build
        sched = yield self.makeScheduler(
            name='n', builderNames=['b', 'c'], codebases={'cb': {'repository': 'http://repo'}}
        )
        yield self.master.startService()

        # No codebaseGenerator means all changes have codebase == ''
        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=10),
            fakedb.SourceStamp(id=11),
            fakedb.SourceStamp(id=12),
            fakedb.Change(changeid=13, codebase='cb', sourcestampid=12),
            fakedb.Change(changeid=14, codebase='cb', sourcestampid=11),
            fakedb.Change(changeid=15, codebase='cb', sourcestampid=10),
        ])

        # note that the changeids are given out of order here; it should still
        # use the most recent
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='power', waited_for=False, changeids=[14, 15, 13]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2],
            external_idstring=None,
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            reason='power',
            scheduler='n',
            sourcestamps=[10],  # sourcestampid from greatest changeid
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_codebases_set_multiple_codebases(self):
        codebases = {
            'cbA': {"repository": 'svn://A..', "branch": 'stable', "revision": '13579'},
            'cbB': {"repository": 'svn://B..', "branch": 'stable', "revision": '24680'},
            'cbC': {"repository": 'svn://C..', "branch": 'stable', "revision": '12345'},
            'cbD': {"repository": 'svn://D..'},
        }
        # Scheduler gets codebases that can be used to create extra sourcestamps
        # for repositories that have no changes
        sched = yield self.makeScheduler(name='n', builderNames=['b', 'c'], codebases=codebases)
        yield self.master.startService()

        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=912),
            fakedb.SourceStamp(id=913),
            fakedb.SourceStamp(id=914),
            fakedb.SourceStamp(id=915),
            fakedb.SourceStamp(id=916),
            fakedb.SourceStamp(id=917),
            fakedb.Change(changeid=12, codebase='cbA', sourcestampid=912),
            fakedb.Change(changeid=13, codebase='cbA', sourcestampid=913),
            fakedb.Change(changeid=14, codebase='cbA', sourcestampid=914),
            fakedb.Change(changeid=15, codebase='cbB', sourcestampid=915),
            fakedb.Change(changeid=16, codebase='cbB', sourcestampid=916),
            fakedb.Change(changeid=17, codebase='cbB', sourcestampid=917),
            # note: no changes for cbC or cbD
        ])

        # note that the changeids are given out of order here; it should still
        # use the most recent for each codebase
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='power', waited_for=True, changeids=[14, 12, 17, 16, 13, 15]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)

        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=True,
            builderids=[1, 2],
            external_idstring=None,
            reason='power',
            scheduler='n',
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            sourcestamps=[
                914,
                917,
                {
                    "branch": 'stable',
                    "repository": 'svn://C..',
                    "codebase": 'cbC',
                    "project": '',
                    "revision": '12345',
                },
                {
                    "branch": None,
                    "repository": 'svn://D..',
                    "codebase": 'cbD',
                    "project": '',
                    "revision": None,
                },
            ],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp(self):
        sched = yield self.makeScheduler(name='n', builderNames=['b'])
        yield self.master.startService()

        sourcestamps = [91, {'sourcestamp': True}]
        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=sourcestamps
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            reason='whynot',
            scheduler='n',
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            sourcestamps=[91, {'sourcestamp': True}],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_explicit_builderNames(self):
        sched = yield self.makeScheduler(name='n', builderNames=['b', 'x', 'y'])
        yield self.master.startService()

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot',
            waited_for=True,
            sourcestamps=[91, {'sourcestamp': True}],
            builderNames=['x', 'y'],
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=True,
            builderids=[2, 3],
            external_idstring=None,
            reason='whynot',
            scheduler='n',
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            sourcestamps=[91, {'sourcestamp': True}],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_properties(self):
        props = properties.Properties(xxx="yyy")
        sched = yield self.makeScheduler(name='n', builderNames=['b'])
        yield self.master.startService()

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[91], properties=props
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={'xxx': ('yyy', 'TEST'), 'scheduler': ('n', 'Scheduler')},
            reason='whynot',
            scheduler='n',
            sourcestamps=[91],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_combine_change_properties(self):
        sched = yield self.makeScheduler()
        yield self.master.startService()

        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=98, branch='stable'),
            fakedb.Change(changeid=25, sourcestampid=98, branch='stable'),
            fakedb.ChangeProperty(
                changeid=25, property_name='color', property_value='["pink","Change"]'
            ),
        ])

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[98]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2],
            external_idstring=None,
            properties={'scheduler': ('testsched', 'Scheduler'), 'color': ('pink', 'Change')},
            reason='whynot',
            scheduler='testsched',
            sourcestamps=[98],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_renderable_builderNames(self):
        @properties.renderer
        def names(props):
            if props.changes[0]['branch'] == 'stable':
                return ['c']
            elif props.changes[0]['branch'] == 'unstable':
                return ['a', 'b']
            return None

        sched = yield self.makeScheduler(name='n', builderNames=names)
        yield self.master.startService()

        yield self.master.db.insert_test_data([
            fakedb.Builder(id=1, name='a'),
            fakedb.Builder(id=2, name='b'),
            fakedb.Builder(id=3, name='c'),
            fakedb.SourceStamp(id=98, branch='stable'),
            fakedb.SourceStamp(id=99, branch='unstable'),
            fakedb.Change(changeid=25, sourcestampid=98, branch='stable'),
            fakedb.Change(changeid=26, sourcestampid=99, branch='unstable'),
        ])

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[98]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[3],
            external_idstring=None,
            properties={'scheduler': ('n', 'Scheduler')},
            reason='whynot',
            scheduler='n',
            sourcestamps=[98],
            priority=0,
        )

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='because', waited_for=False, sourcestamps=[99]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2],
            external_idstring=None,
            properties={'scheduler': ('n', 'Scheduler')},
            reason='because',
            scheduler='n',
            sourcestamps=[99],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_list_of_renderable_builderNames(self):
        names = ['a', 'b', properties.Interpolate('%(prop:extra_builder)s')]
        sched = yield self.makeScheduler(name='n', builderNames=names)
        yield self.master.startService()

        yield self.master.db.insert_test_data([
            fakedb.Builder(id=3, name='c'),
            fakedb.SourceStamp(id=98, branch='stable'),
            fakedb.Change(changeid=25, sourcestampid=98, branch='stable'),
            fakedb.ChangeProperty(
                changeid=25, property_name='extra_builder', property_value='["c","Change"]'
            ),
        ])

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[98]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2, 3],
            external_idstring=None,
            properties={'scheduler': ('n', 'Scheduler'), 'extra_builder': ('c', 'Change')},
            reason='whynot',
            scheduler='n',
            sourcestamps=[98],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_signature_addBuildsetForChanges(self):
        sched = yield self.makeScheduler(builderNames=['xxx'])

        @self.assertArgSpecMatches(
            sched.addBuildsetForChanges,  # Real
            self.fake_addBuildsetForChanges,  # Real
        )
        def addBuildsetForChanges(
            self,
            waited_for=False,
            reason='',
            external_idstring=None,
            changeids=None,
            builderNames=None,
            properties=None,
            priority=None,
            **kw,
        ):
            pass

    @defer.inlineCallbacks
    def test_signature_addBuildsetForSourceStamps(self):
        sched = yield self.makeScheduler(builderNames=['xxx'])

        @self.assertArgSpecMatches(
            sched.addBuildsetForSourceStamps,  # Real
            self.fake_addBuildsetForSourceStamps,  # Fake
        )
        def addBuildsetForSourceStamps(
            self,
            waited_for=False,
            sourcestamps=None,
            reason='',
            external_idstring=None,
            properties=None,
            builderNames=None,
            priority=None,
            **kw,
        ):
            pass

    @defer.inlineCallbacks
    def test_signature_addBuildsetForSourceStampsWithDefaults(self):
        sched = yield self.makeScheduler(builderNames=['xxx'])

        @self.assertArgSpecMatches(
            sched.addBuildsetForSourceStampsWithDefaults,  # Real
            self.fake_addBuildsetForSourceStampsWithDefaults,  # Fake
        )
        def addBuildsetForSourceStampsWithDefaults(
            self,
            reason,
            sourcestamps=None,
            waited_for=False,
            properties=None,
            builderNames=None,
            priority=None,
            **kw,
        ):
            pass


class BaseScheduler(scheduler.SchedulerMixin, TestReactorMixin, unittest.TestCase):
    OBJECTID = 19
    SCHEDULERID = 9
    exp_bsid_brids = (123, {'b': 456})

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        yield self.setUpScheduler()

    @defer.inlineCallbacks
    def makeScheduler(self, name='testsched', builderNames=None, properties=None, codebases=None):
        if builderNames is None:
            builderNames = ['a', 'b']
        if properties is None:
            properties = {}
        if codebases is None:
            codebases = {'': {}}

        if isinstance(builderNames, list):
            dbBuilder = []
            builderid = 0
            for builderName in builderNames:
                if isinstance(builderName, str):
                    builderid += 1
                    dbBuilder.append(fakedb.Builder(id=builderid, name=builderName))

            yield self.master.db.insert_test_data(dbBuilder)

        with assertProducesWarnings(
            DeprecatedApiWarning,
            message_pattern="",
        ):
            sched = base.BaseScheduler(
                name=name, builderNames=builderNames, properties=properties, codebases=codebases
            )
        sched = yield self.attachScheduler(
            sched,
            self.OBJECTID,
            self.SCHEDULERID,
        )
        self.master.data.updates.addBuildset = mock.Mock(
            name='data.addBuildset',
            side_effect=lambda *args, **kwargs: defer.succeed(self.exp_bsid_brids),
        )

        return sched

    # tests

    @defer.inlineCallbacks
    def test_constructor_builderNames(self):
        with self.assertRaises(config.ConfigErrors):
            yield self.makeScheduler(builderNames='xxx')

    @defer.inlineCallbacks
    def test_constructor_builderNames_unicode(self):
        yield self.makeScheduler(builderNames=['a'])

    @defer.inlineCallbacks
    def test_constructor_builderNames_renderable(self):
        @properties.renderer
        def names(props):
            return ['a']

        yield self.makeScheduler(builderNames=names)

    @defer.inlineCallbacks
    def test_constructor_codebases_valid(self):
        codebases = {"codebase1": {"repository": "", "branch": "", "revision": ""}}
        yield self.makeScheduler(codebases=codebases)

    @defer.inlineCallbacks
    def test_constructor_codebases_valid_list(self):
        codebases = ['codebase1']
        yield self.makeScheduler(codebases=codebases)

    @defer.inlineCallbacks
    def test_constructor_codebases_invalid(self):
        # scheduler only accepts codebases with at least repository set
        codebases = {"codebase1": {"dictionary": "", "that": "", "fails": ""}}
        with self.assertRaises(config.ConfigErrors):
            yield self.makeScheduler(codebases=codebases)

    @defer.inlineCallbacks
    def test_getCodebaseDict(self):
        sched = yield self.makeScheduler(codebases={'lib': {'repository': 'librepo'}})
        cbd = yield sched.getCodebaseDict('lib')
        self.assertEqual(cbd, {'repository': 'librepo'})

    @defer.inlineCallbacks
    def test_getCodebaseDict_constructedFromList(self):
        sched = yield self.makeScheduler(codebases=['lib', 'lib2'])
        cbd = yield sched.getCodebaseDict('lib')
        self.assertEqual(cbd, {})

    @defer.inlineCallbacks
    def test_getCodebaseDict_not_found(self):
        sched = yield self.makeScheduler(codebases={'lib': {'repository': 'librepo'}})
        with self.assertRaises(KeyError):
            yield sched.getCodebaseDict('app')

    @defer.inlineCallbacks
    def test_listBuilderNames(self):
        sched = yield self.makeScheduler(builderNames=['x', 'y'])
        self.assertEqual(sched.listBuilderNames(), ['x', 'y'])

    @defer.inlineCallbacks
    def test_startConsumingChanges_fileIsImportant_check(self):
        sched = yield self.makeScheduler()
        try:
            yield sched.startConsumingChanges(fileIsImportant="maybe")
        except AssertionError:
            pass
        else:
            self.fail("didn't assert")

    @defer.inlineCallbacks
    def test_enabled_callback(self):
        sched = yield self.makeScheduler()
        expectedValue = not sched.enabled
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, expectedValue)
        expectedValue = not sched.enabled
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, expectedValue)

    @defer.inlineCallbacks
    def do_test_change_consumption(self, kwargs, expected_result, change_kwargs=None):
        if change_kwargs is None:
            change_kwargs = {}

        # (expected_result should be True (important), False (unimportant), or
        # None (ignore the change))
        sched = yield self.makeScheduler()
        sched.startService()
        self.addCleanup(sched.stopService)

        # set up a change message, a changedict, a change, and convince
        # getChange and fromChdict to convert one to the other
        msg = {"changeid": 12934}

        chdict = {"changeid": 12934, "is_chdict": True}

        def getChange(changeid):
            assert changeid == 12934
            return defer.succeed(chdict)

        self.master.db.changes.getChange = getChange

        change = self.makeFakeChange(**change_kwargs)
        change.number = 12934

        def fromChdict(cls, master, chdict):
            assert chdict['changeid'] == 12934 and chdict['is_chdict']
            return defer.succeed(change)

        self.patch(changes.Change, 'fromChdict', classmethod(fromChdict))

        change_received = [None]

        def gotChange(got_change, got_important):
            # check that we got the expected change object
            self.assertIdentical(got_change, change)
            change_received[0] = got_important
            return defer.succeed(None)

        sched.gotChange = gotChange

        yield sched.startConsumingChanges(**kwargs)

        # check that it registered callbacks
        self.assertEqual(len(self.master.mq.qrefs), 2)

        qref = self.master.mq.qrefs[1]
        self.assertEqual(qref.filter, ('changes', None, 'new'))

        # invoke the callback with the change, and check the result
        qref.callback('change.12934.new', msg)
        self.assertEqual(change_received[0], expected_result)

    def test_change_consumption_defaults(self):
        # all changes are important by default
        return self.do_test_change_consumption({}, True)

    def test_change_consumption_fileIsImportant_True(self):
        return self.do_test_change_consumption({"fileIsImportant": lambda c: True}, True)

    def test_change_consumption_fileIsImportant_False(self):
        return self.do_test_change_consumption({"fileIsImportant": lambda c: False}, False)

    @defer.inlineCallbacks
    def test_change_consumption_fileIsImportant_exception(self):
        yield self.do_test_change_consumption({"fileIsImportant": lambda c: 1 / 0}, None)

        self.assertEqual(1, len(self.flushLoggedErrors(ZeroDivisionError)))

    def test_change_consumption_change_filter_True(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: True
        return self.do_test_change_consumption({"change_filter": cf}, True)

    def test_change_consumption_change_filter_False(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: False
        return self.do_test_change_consumption({"change_filter": cf}, None)

    def test_change_consumption_change_filter_gerrit_ref_updates(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: False
        return self.do_test_change_consumption(
            {'change_filter': cf},
            None,
            change_kwargs={'category': 'ref-updated', 'branch': 'master'},
        )

    def test_change_consumption_change_filter_gerrit_ref_updates_with_refs(self):
        cf = mock.Mock()
        cf.filter_change = lambda c: False
        return self.do_test_change_consumption(
            {'change_filter': cf},
            None,
            change_kwargs={'category': 'ref-updated', 'branch': 'refs/changes/123'},
        )

    def test_change_consumption_change_filter_gerrit_filters_branch_new(self):
        cf = filter.ChangeFilter(branch='master')
        return self.do_test_change_consumption(
            {'change_filter': cf},
            True,
            change_kwargs={'category': 'ref-updated', 'branch': 'master'},
        )

    def test_change_consumption_change_filter_gerrit_filters_branch_new_not_match(self):
        cf = filter.ChangeFilter(branch='other')
        return self.do_test_change_consumption(
            {'change_filter': cf},
            None,
            change_kwargs={'category': 'ref-updated', 'branch': 'master'},
        )

    def test_change_consumption_fileIsImportant_False_onlyImportant(self):
        return self.do_test_change_consumption(
            {"fileIsImportant": lambda c: False, "onlyImportant": True}, None
        )

    def test_change_consumption_fileIsImportant_True_onlyImportant(self):
        return self.do_test_change_consumption(
            {"fileIsImportant": lambda c: True, "onlyImportant": True}, True
        )

    @defer.inlineCallbacks
    def test_activation(self):
        sched = yield self.makeScheduler(name='n', builderNames=['a'])
        sched.activate = mock.Mock(return_value=defer.succeed(None))
        sched.deactivate = mock.Mock(return_value=defer.succeed(None))

        # set the schedulerid, and claim the scheduler on another master
        yield self.setSchedulerToMaster(self.OTHER_MASTER_ID)

        yield sched.startService()
        self.reactor.advance(sched.POLL_INTERVAL_SEC / 2)
        self.reactor.advance(sched.POLL_INTERVAL_SEC / 5)
        self.reactor.advance(sched.POLL_INTERVAL_SEC / 5)
        self.assertFalse(sched.activate.called)
        self.assertFalse(sched.deactivate.called)
        self.assertFalse(sched.isActive())
        # objectid is attached by the test helper
        self.assertEqual(sched.serviceid, self.SCHEDULERID)

        # clear that masterid
        yield sched.stopService()
        yield self.setSchedulerToMaster(None)
        yield sched.startService()
        self.reactor.advance(sched.POLL_INTERVAL_SEC)
        self.assertTrue(sched.activate.called)
        self.assertFalse(sched.deactivate.called)
        self.assertTrue(sched.isActive())

        # stop the service and see that deactivate is called
        yield sched.stopService()
        self.assertTrue(sched.activate.called)
        self.assertTrue(sched.deactivate.called)
        self.assertFalse(sched.isActive())

    @defer.inlineCallbacks
    def test_activation_claim_raises(self):
        sched = yield self.makeScheduler(name='n', builderNames=['a'])

        # set the schedulerid, and claim the scheduler on another master
        yield self.setSchedulerToMaster(self.OTHER_MASTER_ID)

        sched.startService()
        self.assertFalse(sched.isActive())

    @defer.inlineCallbacks
    def test_activation_activate_fails(self):
        sched = yield self.makeScheduler(name='n', builderNames=['a'])

        def activate():
            raise RuntimeError('oh noes')

        sched.activate = activate

        yield sched.startService()
        self.assertEqual(1, len(self.flushLoggedErrors(RuntimeError)))

    @defer.inlineCallbacks
    def do_addBuildsetForSourceStampsWithDefaults(self, codebases, sourcestamps, exp_sourcestamps):
        sched = yield self.makeScheduler(name='n', builderNames=['b'], codebases=codebases)
        bsid, brids = yield sched.addBuildsetForSourceStampsWithDefaults(
            reason='power', sourcestamps=sourcestamps, waited_for=False
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        call = self.master.data.updates.addBuildset.mock_calls[0]

        def sourceStampKey(sourceStamp):
            repository = sourceStamp.get('repository', '')
            if repository is None:
                repository = ''
            branch = sourceStamp.get('branch', '') if not None else ''
            if branch is None:
                branch = ''
            return (repository, branch)

        self.assertEqual(
            sorted(call[2]['sourcestamps'], key=sourceStampKey),
            sorted(exp_sourcestamps, key=sourceStampKey),
        )

    def test_addBuildsetForSourceStampsWithDefaults(self):
        codebases = {
            'cbA': {"repository": 'svn://A..', "branch": 'stable', "revision": '13579'},
            'cbB': {"repository": 'svn://B..', "branch": 'stable', "revision": '24680'},
        }
        sourcestamps = [
            {'codebase': 'cbA', 'branch': 'AA'},
            {'codebase': 'cbB', 'revision': 'BB'},
        ]
        exp_sourcestamps = [
            {
                'repository': 'svn://B..',
                'branch': 'stable',
                'revision': 'BB',
                'codebase': 'cbB',
                'project': '',
            },
            {
                'repository': 'svn://A..',
                'branch': 'AA',
                'project': '',
                'revision': '13579',
                'codebase': 'cbA',
            },
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults(
            codebases, sourcestamps, exp_sourcestamps
        )

    def test_addBuildsetForSourceStampsWithDefaults_fill_in_codebases(self):
        codebases = {
            'cbA': {"repository": 'svn://A..', "branch": 'stable', "revision": '13579'},
            'cbB': {"repository": 'svn://B..', "branch": 'stable', "revision": '24680'},
        }
        sourcestamps = [
            {'codebase': 'cbA', 'branch': 'AA'},
        ]
        exp_sourcestamps = [
            {
                'repository': 'svn://B..',
                'branch': 'stable',
                'revision': '24680',
                'codebase': 'cbB',
                'project': '',
            },
            {
                'repository': 'svn://A..',
                'branch': 'AA',
                'project': '',
                'revision': '13579',
                'codebase': 'cbA',
            },
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults(
            codebases, sourcestamps, exp_sourcestamps
        )

    def test_addBuildsetForSourceStampsWithDefaults_no_repository(self):
        exp_sourcestamps = [
            {'repository': '', 'branch': None, 'revision': None, 'codebase': '', 'project': ''},
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults({'': {}}, [], exp_sourcestamps)

    def test_addBuildsetForSourceStamps_unknown_codbases(self):
        codebases = {}
        sourcestamps = [
            {'codebase': 'cbA', 'branch': 'AA'},
            {'codebase': 'cbB', 'revision': 'BB'},
        ]
        exp_sourcestamps = [
            {'branch': None, 'revision': 'BB', 'codebase': 'cbB', 'project': '', 'repository': ''},
            {'branch': 'AA', 'revision': None, 'codebase': 'cbA', 'project': '', 'repository': ''},
        ]
        return self.do_addBuildsetForSourceStampsWithDefaults(
            codebases, sourcestamps, exp_sourcestamps
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_one_change(self):
        sched = yield self.makeScheduler(name='n', builderNames=['b'])
        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=234),
            fakedb.Change(changeid=13, sourcestampid=234),
        ])
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='power', waited_for=False, changeids=[13]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            reason='power',
            scheduler='n',
            sourcestamps=[234],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_properties(self):
        sched = yield self.makeScheduler(name='n', builderNames=['c'])
        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=234),
            fakedb.Change(changeid=14, sourcestampid=234),
        ])
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='downstream', waited_for=False, changeids=[14]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            reason='downstream',
            scheduler='n',
            sourcestamps=[234],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_properties_with_virtual_builders(self):
        sched = yield self.makeScheduler(
            name='n',
            builderNames=['c'],
            properties={'virtual_builder_name': Interpolate("myproject-%(src::branch)s")},
        )
        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=234, branch='dev1', project="linux"),
            fakedb.Change(changeid=14, sourcestampid=234, branch="dev1"),
        ])
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='downstream', waited_for=False, changeids=[14]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={
                'virtual_builder_name': ("myproject-dev1", "Scheduler"),
                'scheduler': ('n', 'Scheduler'),
            },
            reason='downstream',
            scheduler='n',
            sourcestamps=[234],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_multiple_changes_same_codebase(self):
        # This is a test for backwards compatibility
        # Changes from different repositories come together in one build
        sched = yield self.makeScheduler(
            name='n', builderNames=['b', 'c'], codebases={'cb': {'repository': 'http://repo'}}
        )
        # No codebaseGenerator means all changes have codebase == ''
        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=10),
            fakedb.SourceStamp(id=11),
            fakedb.SourceStamp(id=12),
            fakedb.Change(changeid=13, codebase='cb', sourcestampid=12),
            fakedb.Change(changeid=14, codebase='cb', sourcestampid=11),
            fakedb.Change(changeid=15, codebase='cb', sourcestampid=10),
        ])

        # note that the changeids are given out of order here; it should still
        # use the most recent
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='power', waited_for=False, changeids=[14, 15, 13]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2],
            external_idstring=None,
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            reason='power',
            scheduler='n',
            sourcestamps=[10],  # sourcestampid from greatest changeid
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForChanges_codebases_set_multiple_codebases(self):
        codebases = {
            'cbA': {"repository": 'svn://A..', "branch": 'stable', "revision": '13579'},
            'cbB': {"repository": 'svn://B..', "branch": 'stable', "revision": '24680'},
            'cbC': {"repository": 'svn://C..', "branch": 'stable', "revision": '12345'},
            'cbD': {"repository": 'svn://D..'},
        }
        # Scheduler gets codebases that can be used to create extra sourcestamps
        # for repositories that have no changes
        sched = yield self.makeScheduler(name='n', builderNames=['b', 'c'], codebases=codebases)
        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=912),
            fakedb.SourceStamp(id=913),
            fakedb.SourceStamp(id=914),
            fakedb.SourceStamp(id=915),
            fakedb.SourceStamp(id=916),
            fakedb.SourceStamp(id=917),
            fakedb.Change(changeid=12, codebase='cbA', sourcestampid=912),
            fakedb.Change(changeid=13, codebase='cbA', sourcestampid=913),
            fakedb.Change(changeid=14, codebase='cbA', sourcestampid=914),
            fakedb.Change(changeid=15, codebase='cbB', sourcestampid=915),
            fakedb.Change(changeid=16, codebase='cbB', sourcestampid=916),
            fakedb.Change(changeid=17, codebase='cbB', sourcestampid=917),
            # note: no changes for cbC or cbD
        ])

        # note that the changeids are given out of order here; it should still
        # use the most recent for each codebase
        bsid, brids = yield sched.addBuildsetForChanges(
            reason='power', waited_for=True, changeids=[14, 12, 17, 16, 13, 15]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)

        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=True,
            builderids=[1, 2],
            external_idstring=None,
            reason='power',
            scheduler='n',
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            sourcestamps=[
                914,
                917,
                {
                    "branch": 'stable',
                    "repository": 'svn://C..',
                    "codebase": 'cbC',
                    "project": '',
                    "revision": '12345',
                },
                {
                    "branch": None,
                    "repository": 'svn://D..',
                    "codebase": 'cbD',
                    "project": '',
                    "revision": None,
                },
            ],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp(self):
        sched = yield self.makeScheduler(name='n', builderNames=['b'])
        sourcestamps = [91, {'sourcestamp': True}]
        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=sourcestamps
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            reason='whynot',
            scheduler='n',
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            sourcestamps=[91, {'sourcestamp': True}],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_explicit_builderNames(self):
        sched = yield self.makeScheduler(name='n', builderNames=['b', 'x', 'y'])
        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot',
            waited_for=True,
            sourcestamps=[91, {'sourcestamp': True}],
            builderNames=['x', 'y'],
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=True,
            builderids=[2, 3],
            external_idstring=None,
            reason='whynot',
            scheduler='n',
            properties={
                'scheduler': ('n', 'Scheduler'),
            },
            sourcestamps=[91, {'sourcestamp': True}],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_properties(self):
        props = properties.Properties(xxx="yyy")
        sched = yield self.makeScheduler(name='n', builderNames=['b'])
        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[91], properties=props
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1],
            external_idstring=None,
            properties={'xxx': ('yyy', 'TEST'), 'scheduler': ('n', 'Scheduler')},
            reason='whynot',
            scheduler='n',
            sourcestamps=[91],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_combine_change_properties(self):
        sched = yield self.makeScheduler()

        yield self.master.db.insert_test_data([
            fakedb.SourceStamp(id=98, branch='stable'),
            fakedb.Change(changeid=25, sourcestampid=98, branch='stable'),
            fakedb.ChangeProperty(
                changeid=25, property_name='color', property_value='["pink","Change"]'
            ),
        ])

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[98]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2],
            external_idstring=None,
            properties={'scheduler': ('testsched', 'Scheduler'), 'color': ('pink', 'Change')},
            reason='whynot',
            scheduler='testsched',
            sourcestamps=[98],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_renderable_builderNames(self):
        @properties.renderer
        def names(props):
            if props.changes[0]['branch'] == 'stable':
                return ['c']
            elif props.changes[0]['branch'] == 'unstable':
                return ['a', 'b']
            return None

        sched = yield self.makeScheduler(name='n', builderNames=names)

        yield self.master.db.insert_test_data([
            fakedb.Builder(id=1, name='a'),
            fakedb.Builder(id=2, name='b'),
            fakedb.Builder(id=3, name='c'),
            fakedb.SourceStamp(id=98, branch='stable'),
            fakedb.SourceStamp(id=99, branch='unstable'),
            fakedb.Change(changeid=25, sourcestampid=98, branch='stable'),
            fakedb.Change(changeid=26, sourcestampid=99, branch='unstable'),
        ])

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[98]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[3],
            external_idstring=None,
            properties={'scheduler': ('n', 'Scheduler')},
            reason='whynot',
            scheduler='n',
            sourcestamps=[98],
            priority=0,
        )

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='because', waited_for=False, sourcestamps=[99]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2],
            external_idstring=None,
            properties={'scheduler': ('n', 'Scheduler')},
            reason='because',
            scheduler='n',
            sourcestamps=[99],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_addBuildsetForSourceStamp_list_of_renderable_builderNames(self):
        names = ['a', 'b', properties.Interpolate('%(prop:extra_builder)s')]
        sched = yield self.makeScheduler(name='n', builderNames=names)

        yield self.master.db.insert_test_data([
            fakedb.Builder(id=3, name='c'),
            fakedb.SourceStamp(id=98, branch='stable'),
            fakedb.Change(changeid=25, sourcestampid=98, branch='stable'),
            fakedb.ChangeProperty(
                changeid=25, property_name='extra_builder', property_value='["c","Change"]'
            ),
        ])

        bsid, brids = yield sched.addBuildsetForSourceStamps(
            reason='whynot', waited_for=False, sourcestamps=[98]
        )
        self.assertEqual((bsid, brids), self.exp_bsid_brids)
        self.master.data.updates.addBuildset.assert_called_with(
            waited_for=False,
            builderids=[1, 2, 3],
            external_idstring=None,
            properties={'scheduler': ('n', 'Scheduler'), 'extra_builder': ('c', 'Change')},
            reason='whynot',
            scheduler='n',
            sourcestamps=[98],
            priority=0,
        )

    @defer.inlineCallbacks
    def test_signature_addBuildsetForChanges(self):
        sched = yield self.makeScheduler(builderNames=['xxx'])

        @self.assertArgSpecMatches(
            sched.addBuildsetForChanges,  # Real
            self.fake_addBuildsetForChanges,  # Real
        )
        def addBuildsetForChanges(
            self,
            waited_for=False,
            reason='',
            external_idstring=None,
            changeids=None,
            builderNames=None,
            properties=None,
            priority=None,
            **kw,
        ):
            pass

    @defer.inlineCallbacks
    def test_signature_addBuildsetForSourceStamps(self):
        sched = yield self.makeScheduler(builderNames=['xxx'])

        @self.assertArgSpecMatches(
            sched.addBuildsetForSourceStamps,  # Real
            self.fake_addBuildsetForSourceStamps,  # Fake
        )
        def addBuildsetForSourceStamps(
            self,
            waited_for=False,
            sourcestamps=None,
            reason='',
            external_idstring=None,
            properties=None,
            builderNames=None,
            priority=None,
            **kw,
        ):
            pass

    @defer.inlineCallbacks
    def test_signature_addBuildsetForSourceStampsWithDefaults(self):
        sched = yield self.makeScheduler(builderNames=['xxx'])

        @self.assertArgSpecMatches(
            sched.addBuildsetForSourceStampsWithDefaults,  # Real
            self.fake_addBuildsetForSourceStampsWithDefaults,  # Fake
        )
        def addBuildsetForSourceStampsWithDefaults(
            self,
            reason,
            sourcestamps=None,
            waited_for=False,
            properties=None,
            builderNames=None,
            priority=None,
            **kw,
        ):
            pass
