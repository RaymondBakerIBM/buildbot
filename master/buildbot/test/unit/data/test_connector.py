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
from twisted.python import reflect
from twisted.trial import unittest

from buildbot.data import base
from buildbot.data import connector
from buildbot.data import exceptions
from buildbot.data import resultspec
from buildbot.data import types
from buildbot.test.fake import fakemaster
from buildbot.test.reactor import TestReactorMixin
from buildbot.test.util import interfaces
from buildbot.test.util.warnings import assertProducesWarnings
from buildbot.warnings import DeprecatedApiWarning


class Tests(interfaces.InterfaceTests):
    def setUp(self):
        raise NotImplementedError

    def test_signature_get(self):
        @self.assertArgSpecMatches(self.data.get)
        def get(
            self,
            path,
            filters=None,
            fields=None,
            order=None,
            limit=None,
            offset=None,
            properties=None,
        ):
            pass

    def test_signature_getEndpoint(self):
        @self.assertArgSpecMatches(self.data.getEndpoint)
        def getEndpoint(self, path):
            pass

    def test_signature_control(self):
        @self.assertArgSpecMatches(self.data.control)
        def control(self, action, args, path):
            pass

    def test_signature_updates_addChange(self):
        @self.assertArgSpecMatches(self.data.updates.addChange)
        def addChange(
            self,
            files=None,
            comments=None,
            author=None,
            committer=None,
            revision=None,
            when_timestamp=None,
            branch=None,
            category=None,
            revlink='',
            properties=None,
            repository='',
            codebase=None,
            project='',
            src=None,
        ):
            pass

    def test_signature_updates_masterActive(self):
        @self.assertArgSpecMatches(self.data.updates.masterActive)
        def masterActive(self, name, masterid):
            pass

    def test_signature_updates_masterStopped(self):
        @self.assertArgSpecMatches(self.data.updates.masterStopped)
        def masterStopped(self, name, masterid):
            pass

    def test_signature_updates_addBuildset(self):
        @self.assertArgSpecMatches(self.data.updates.addBuildset)
        def addBuildset(
            self,
            waited_for,
            scheduler=None,
            sourcestamps=None,
            reason='',
            properties=None,
            builderids=None,
            external_idstring=None,
            rebuilt_buildid=None,
            parent_buildid=None,
            parent_relationship=None,
            priority=0,
        ):
            pass

    def test_signature_updates_maybeBuildsetComplete(self):
        @self.assertArgSpecMatches(self.data.updates.maybeBuildsetComplete)
        def maybeBuildsetComplete(self, bsid):
            pass

    def test_signature_updates_updateBuilderList(self):
        @self.assertArgSpecMatches(self.data.updates.updateBuilderList)
        def updateBuilderList(self, masterid, builderNames):
            pass


class TestFakeData(TestReactorMixin, unittest.TestCase, Tests):
    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = yield fakemaster.make_master(self, wantMq=True, wantData=True, wantDb=True)
        self.data = self.master.data


class TestDataConnector(TestReactorMixin, unittest.TestCase, Tests):
    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = yield fakemaster.make_master(self, wantMq=True)
        self.data = connector.DataConnector()
        yield self.data.setServiceParent(self.master)


class DataConnector(TestReactorMixin, unittest.TestCase):
    maxDiff = None

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = yield fakemaster.make_master(self)
        # don't load by default
        self.patch(connector.DataConnector, 'submodules', [])
        self.data = connector.DataConnector()
        yield self.data.setServiceParent(self.master)

    def patchFooPattern(self):
        cls = type('FooEndpoint', (base.Endpoint,), {})
        ep = cls(None, self.master)
        ep.get = mock.Mock(name='FooEndpoint.get')
        ep.get.return_value = defer.succeed({'val': 9999})
        self.data.matcher[('foo', 'n:fooid', 'bar')] = ep
        return ep

    def patchFooListPattern(self):
        cls = type('FoosEndpoint', (base.Endpoint,), {})
        ep = cls(None, self.master)
        ep.get = mock.Mock(name='FoosEndpoint.get')
        ep.get.return_value = defer.succeed([{'val': v} for v in range(900, 920)])
        self.data.matcher[('foo',)] = ep
        return ep

    # tests

    def test_sets_master(self):
        self.assertIdentical(self.master, self.data.master)

    def test_scanModule(self):
        # use this module as a test
        mod = reflect.namedModule('buildbot.test.unit.data.test_connector')
        self.data._scanModule(mod)

        # check that it discovered MyResourceType and updated endpoints
        match = self.data.matcher[('test', '10')]
        self.assertIsInstance(match[0], TestEndpoint)
        self.assertEqual(match[1], {"testid": 10})
        match = self.data.matcher[('test', '10', 'p1')]
        self.assertIsInstance(match[0], TestEndpoint)
        match = self.data.matcher[('test', '10', 'p2')]
        self.assertIsInstance(match[0], TestEndpoint)
        match = self.data.matcher[('tests',)]
        self.assertIsInstance(match[0], TestsEndpoint)
        self.assertEqual(match[1], {})
        match = self.data.matcher[('test', 'foo')]
        self.assertIsInstance(match[0], TestsEndpointSubclass)
        self.assertEqual(match[1], {})

        # and that it found the update method
        self.assertEqual(self.data.updates.testUpdate(), "testUpdate return")

        # and that it added the single root link
        self.assertEqual(self.data.rootLinks, [{'name': 'tests'}])

        # and that it added an attribute
        self.assertIsInstance(self.data.rtypes.test, TestResourceType)

    def test_scanModule_path_pattern_multiline_string_deprecation(self):
        mod = reflect.namedModule('buildbot.test.unit.data.test_connector')

        TestEndpoint.pathPatterns = """
            /test/n:testid
            /test/n:testid/p1
            /test/n:testid/p2
        """

        with assertProducesWarnings(
            DeprecatedApiWarning,
            message_pattern='.*Endpoint.pathPatterns as a multiline string is deprecated.*',
        ):
            self.data._scanModule(mod)

    def test_getEndpoint(self):
        ep = self.patchFooPattern()
        got = self.data.getEndpoint(('foo', '10', 'bar'))
        self.assertEqual(got, (ep, {'fooid': 10}))

    def test_getEndpoint_missing(self):
        with self.assertRaises(exceptions.InvalidPathError):
            self.data.getEndpoint(('xyz',))

    @defer.inlineCallbacks
    def test_get(self):
        ep = self.patchFooPattern()
        gotten = yield self.data.get(('foo', '10', 'bar'))

        self.assertEqual(gotten, {'val': 9999})
        ep.get.assert_called_once_with(mock.ANY, {'fooid': 10})

    @defer.inlineCallbacks
    def test_get_filters(self):
        ep = self.patchFooListPattern()
        gotten = yield self.data.get(('foo',), filters=[resultspec.Filter('val', 'lt', [902])])

        self.assertEqual(gotten, base.ListResult([{'val': 900}, {'val': 901}], total=2))
        ep.get.assert_called_once_with(mock.ANY, {})

    @defer.inlineCallbacks
    def test_get_resultSpec_args(self):
        ep = self.patchFooListPattern()
        f = resultspec.Filter('val', 'gt', [909])
        gotten = yield self.data.get(('foo',), filters=[f], fields=['val'], order=['-val'], limit=2)

        self.assertEqual(gotten, base.ListResult([{'val': 919}, {'val': 918}], total=10, limit=2))
        ep.get.assert_called_once_with(mock.ANY, {})

    @defer.inlineCallbacks
    def test_control(self):
        ep = self.patchFooPattern()
        ep.control = mock.Mock(name='MyEndpoint.control')
        ep.control.return_value = defer.succeed('controlled')

        gotten = yield self.data.control('foo!', {'arg': 2}, ('foo', '10', 'bar'))

        self.assertEqual(gotten, 'controlled')
        ep.control.assert_called_once_with('foo!', {'arg': 2}, {'fooid': 10})


# classes discovered by test_scanModule, above


class TestsEndpoint(base.Endpoint):
    pathPatterns = [
        "/tests",
    ]
    rootLinkName = 'tests'


class TestsEndpointParentClass(base.Endpoint):
    rootLinkName = 'shouldnt-see-this'


class TestsEndpointSubclass(TestsEndpointParentClass):
    pathPatterns = [
        "/test/foo",
    ]


class TestEndpoint(base.Endpoint):
    pathPatterns = [
        "/test/n:testid",
        "/test/n:testid/p1",
        "/test/n:testid/p2",
    ]


class TestResourceType(base.ResourceType):
    name = 'test'
    plural = 'tests'

    endpoints = [TestsEndpoint, TestEndpoint, TestsEndpointSubclass]

    class EntityType(types.Entity):
        testid = types.Integer()

    entityType = EntityType(name)

    @base.updateMethod
    def testUpdate(self):
        return "testUpdate return"
