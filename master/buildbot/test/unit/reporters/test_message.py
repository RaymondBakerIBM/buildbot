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

import textwrap
from unittest import mock

from twisted.internet import defer
from twisted.trial import unittest

from buildbot import config
from buildbot.process.properties import Interpolate
from buildbot.process.results import CANCELLED
from buildbot.process.results import EXCEPTION
from buildbot.process.results import FAILURE
from buildbot.process.results import RETRY
from buildbot.process.results import SKIPPED
from buildbot.process.results import SUCCESS
from buildbot.process.results import WARNINGS
from buildbot.reporters import message
from buildbot.reporters import utils
from buildbot.test import fakedb
from buildbot.test.fake import fakemaster
from buildbot.test.reactor import TestReactorMixin
from buildbot.test.util.misc import BuildDictLookAlike


class TestMessageFormatting(unittest.TestCase):
    def test_get_detected_status_text_failure(self):
        self.assertEqual(
            message.get_detected_status_text(['change'], FAILURE, FAILURE), 'failed build'
        )
        self.assertEqual(
            message.get_detected_status_text(['change'], FAILURE, SUCCESS), 'new failure'
        )
        self.assertEqual(
            message.get_detected_status_text(['change'], FAILURE, None), 'failed build'
        )
        self.assertEqual(
            message.get_detected_status_text(['problem'], FAILURE, FAILURE), 'failed build'
        )
        self.assertEqual(
            message.get_detected_status_text(['problem'], FAILURE, SUCCESS), 'new failure'
        )
        self.assertEqual(
            message.get_detected_status_text(['problem'], FAILURE, None), 'failed build'
        )

    def test_get_detected_status_text_warnings(self):
        self.assertEqual(
            message.get_detected_status_text(['change'], WARNINGS, SUCCESS), 'problem in the build'
        )
        self.assertEqual(
            message.get_detected_status_text(['change'], WARNINGS, None), 'problem in the build'
        )

    def test_get_detected_status_text_success(self):
        self.assertEqual(
            message.get_detected_status_text(['change'], SUCCESS, FAILURE), 'restored build'
        )
        self.assertEqual(
            message.get_detected_status_text(['change'], SUCCESS, SUCCESS), 'passing build'
        )
        self.assertEqual(
            message.get_detected_status_text(['change'], SUCCESS, None), 'passing build'
        )

        self.assertEqual(
            message.get_detected_status_text(['problem'], SUCCESS, FAILURE), 'passing build'
        )
        self.assertEqual(
            message.get_detected_status_text(['problem'], SUCCESS, SUCCESS), 'passing build'
        )
        self.assertEqual(
            message.get_detected_status_text(['problem'], SUCCESS, None), 'passing build'
        )

    def test_get_detected_status_text_exception(self):
        self.assertEqual(
            message.get_detected_status_text(['problem'], EXCEPTION, FAILURE), 'build exception'
        )
        self.assertEqual(
            message.get_detected_status_text(['problem'], EXCEPTION, SUCCESS), 'build exception'
        )
        self.assertEqual(
            message.get_detected_status_text(['problem'], EXCEPTION, None), 'build exception'
        )

    def test_get_detected_status_text_other(self):
        self.assertEqual(
            message.get_detected_status_text(['problem'], SKIPPED, None), 'skipped build'
        )
        self.assertEqual(message.get_detected_status_text(['problem'], RETRY, None), 'retry build')
        self.assertEqual(
            message.get_detected_status_text(['problem'], CANCELLED, None), 'cancelled build'
        )

    def test_get_message_summary_text_success(self):
        self.assertEqual(
            message.get_message_summary_text({'state_string': 'mywarning'}, SUCCESS),
            'Build succeeded!',
        )

    def test_get_message_summary_text_warnings(self):
        self.assertEqual(
            message.get_message_summary_text({'state_string': 'mywarning'}, WARNINGS),
            'Build Had Warnings: mywarning',
        )
        self.assertEqual(
            message.get_message_summary_text({'state_string': None}, WARNINGS), 'Build Had Warnings'
        )

    def test_get_message_summary_text_cancelled(self):
        self.assertEqual(
            message.get_message_summary_text({'state_string': 'mywarning'}, CANCELLED),
            'Build was cancelled',
        )

    def test_get_message_summary_text_skipped(self):
        self.assertEqual(
            message.get_message_summary_text({'state_string': 'mywarning'}, SKIPPED),
            'BUILD FAILED: mywarning',
        )
        self.assertEqual(
            message.get_message_summary_text({'state_string': None}, SKIPPED), 'BUILD FAILED'
        )

    def test_get_message_source_stamp_text_empty(self):
        self.assertEqual(message.get_message_source_stamp_text([]), '')

    def test_get_message_source_stamp_text_multiple(self):
        stamps = [
            {'codebase': 'a', 'branch': None, 'revision': None, 'patch': None},
            {'codebase': 'b', 'branch': None, 'revision': None, 'patch': None},
        ]
        self.assertEqual(
            message.get_message_source_stamp_text(stamps),
            "Build Source Stamp 'a': HEAD\nBuild Source Stamp 'b': HEAD\n",
        )

    def test_get_message_source_stamp_text_with_props(self):
        stamps = [{'codebase': 'a', 'branch': 'br', 'revision': 'abc', 'patch': 'patch'}]
        self.assertEqual(
            message.get_message_source_stamp_text(stamps),
            "Build Source Stamp 'a': [branch br] abc (plus patch)\n",
        )


class MessageFormatterTestBase(TestReactorMixin, unittest.TestCase):
    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = yield fakemaster.make_master(self, wantData=True, wantDb=True, wantMq=True)

    @defer.inlineCallbacks
    def setup_db(self, results1, results2, with_steps=False, extra_build_properties=None):
        if extra_build_properties is None:
            extra_build_properties = {}

        yield self.master.db.insert_test_data([
            fakedb.Master(id=92),
            fakedb.Worker(id=13, name='wrkr'),
            fakedb.Buildset(id=98, results=results1, reason="testReason1"),
            fakedb.Buildset(id=99, results=results2, reason="testReason2"),
            fakedb.SourceStamp(id=1, branch='master', revision='1234abcd'),
            fakedb.BuildsetSourceStamp(buildsetid=98, sourcestampid=1),
            fakedb.BuildsetSourceStamp(buildsetid=99, sourcestampid=1),
            fakedb.Builder(id=80, name='Builder1'),
            fakedb.BuildRequest(id=11, buildsetid=98, builderid=80),
            fakedb.BuildRequest(id=12, buildsetid=99, builderid=80),
            fakedb.Build(
                id=20,
                number=0,
                builderid=80,
                buildrequestid=11,
                workerid=13,
                masterid=92,
                results=results1,
            ),
            fakedb.Build(
                id=21,
                number=1,
                builderid=80,
                buildrequestid=12,
                workerid=13,
                masterid=92,
                results=results2,
            ),
        ])
        for build_id in (20, 21):
            yield self.master.db.insert_test_data([
                fakedb.BuildProperty(buildid=build_id, name="workername", value="wrkr"),
                fakedb.BuildProperty(buildid=build_id, name="reason", value="because"),
            ])

            for name, value in extra_build_properties.items():
                yield self.master.db.insert_test_data([
                    fakedb.BuildProperty(buildid=build_id, name=name, value=value),
                ])

        if with_steps:
            yield self.master.db.insert_test_data([
                fakedb.Step(id=151, buildid=21, number=1, results=SUCCESS, name='first step'),
                fakedb.Step(id=152, buildid=21, number=2, results=results2, name='second step'),
                fakedb.Step(id=153, buildid=21, number=3, results=SUCCESS, name='third step'),
                fakedb.Log(id=251, stepid=152, name='stdio', slug='stdio', type='s', num_lines=7),
                fakedb.Log(id=252, stepid=152, name='stderr', slug='stderr', type='s', num_lines=7),
                fakedb.Log(id=253, stepid=153, name='stdio', slug='stdio', type='s', num_lines=7),
            ])

    @defer.inlineCallbacks
    def do_one_test(
        self,
        formatter,
        lastresults,
        results,
        mode="all",
        with_steps=False,
        extra_build_properties=None,
    ):
        yield self.setup_db(
            lastresults,
            results,
            with_steps=with_steps,
            extra_build_properties=extra_build_properties,
        )

        res = yield utils.getDetailsForBuildset(
            self.master,
            99,
            want_properties=formatter.want_properties,
            want_steps=formatter.want_steps,
            want_previous_build=True,
            want_logs=formatter.want_logs,
            want_logs_content=formatter.want_logs_content,
        )

        build = res['builds'][0]
        res = yield formatter.format_message_for_build(
            self.master, build, mode=mode, users=["him@bar", "me@foo"]
        )
        return res

    @defer.inlineCallbacks
    def do_one_test_buildset(
        self,
        formatter,
        lastresults,
        results,
        mode="all",
        with_steps=False,
        extra_build_properties=None,
    ):
        yield self.setup_db(
            lastresults,
            results,
            with_steps=with_steps,
            extra_build_properties=extra_build_properties,
        )

        res = yield utils.getDetailsForBuildset(
            self.master,
            99,
            want_properties=formatter.want_properties,
            want_steps=formatter.want_steps,
            want_previous_build=True,
            want_logs=formatter.want_logs,
            want_logs_content=formatter.want_logs_content,
        )

        res = yield formatter.format_message_for_buildset(
            self.master, res["buildset"], res["builds"], mode=mode, users=["him@bar", "me@foo"]
        )
        return res


class TestMessageFormatter(MessageFormatterTestBase):
    def test_unknown_template_type_for_default_message(self):
        with self.assertRaises(config.ConfigErrors):
            message.MessageFormatter(template_type='unknown')

    @defer.inlineCallbacks
    def test_message_success_plain_no_steps(self):
        formatter = message.MessageFormatter()
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS)

        self.assertEqual(
            res,
            {
                'type': 'plain',
                'subject': '☺ Buildbot (Buildbot): Builder1 - test ((unknown revision))',
                "extra_info": None,
                'body': textwrap.dedent("""\
                A passing build has been detected on builder Builder1 while building proj.

                Full details are available at:
                    http://localhost:8080/#/builders/80/builds/1

                Build state: test
                Revision: (unknown)
                Worker: wrkr
                Build Reason: because
                Blamelist: him@bar, me@foo

                Steps:

                - (no steps)
                """),
            },
        )

    @defer.inlineCallbacks
    def test_message_success_plain_with_steps(self):
        formatter = message.MessageFormatter()
        res = yield self.do_one_test(
            formatter,
            SUCCESS,
            SUCCESS,
            with_steps=True,
            extra_build_properties={'got_revision': 'abcd1234'},
        )
        self.assertEqual(
            res,
            {
                'type': 'plain',
                'subject': '☺ Buildbot (Buildbot): Builder1 - test (abcd1234)',
                "extra_info": None,
                'body': textwrap.dedent("""\
                A passing build has been detected on builder Builder1 while building proj.

                Full details are available at:
                    http://localhost:8080/#/builders/80/builds/1

                Build state: test
                Revision: abcd1234
                Worker: wrkr
                Build Reason: because
                Blamelist: him@bar, me@foo

                Steps:

                - 1: first step ( success )

                - 2: second step ( success )
                    Logs:
                        - stdio: http://localhost:8080/#/builders/80/builds/1/steps/2/logs/stdio
                        - stderr: http://localhost:8080/#/builders/80/builds/1/steps/2/logs/stderr

                - 3: third step ( success )
                    Logs:
                        - stdio: http://localhost:8080/#/builders/80/builds/1/steps/3/logs/stdio

                """),
            },
        )

    @defer.inlineCallbacks
    def test_message_success_html(self):
        formatter = message.MessageFormatter(template_type='html')
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS)
        self.assertEqual(
            res,
            {
                'type': 'html',
                'subject': '☺ Buildbot (Buildbot): Builder1 - test ((unknown revision))',
                "extra_info": None,
                'body': textwrap.dedent("""\
                <p>A passing build has been detected on builder
                <a href="http://localhost:8080/#/builders/80/builds/1">Builder1</a>
                while building proj.</p>
                <p>Information:</p>
                <ul>
                    <li>Build state: test</li>
                    <li>Revision: (unknown)</li>
                    <li>Worker: wrkr</li>
                    <li>Build Reason: because</li>
                    <li>Blamelist: him@bar, me@foo</li>
                </ul>
                <p>Steps:</p>
                <ul>

                    <li>No steps</li>

                </ul>"""),
            },
        )

    @defer.inlineCallbacks
    def test_message_success_html_with_steps(self):
        formatter = message.MessageFormatter(template_type='html')
        res = yield self.do_one_test(
            formatter,
            SUCCESS,
            SUCCESS,
            with_steps=True,
            extra_build_properties={'got_revision': 'abcd1234'},
        )
        self.assertEqual(
            res,
            {
                'type': 'html',
                'subject': '☺ Buildbot (Buildbot): Builder1 - test (abcd1234)',
                "extra_info": None,
                'body': textwrap.dedent("""\
                <p>A passing build has been detected on builder
                <a href="http://localhost:8080/#/builders/80/builds/1">Builder1</a>
                while building proj.</p>
                <p>Information:</p>
                <ul>
                    <li>Build state: test</li>
                    <li>Revision: abcd1234</li>
                    <li>Worker: wrkr</li>
                    <li>Build Reason: because</li>
                    <li>Blamelist: him@bar, me@foo</li>
                </ul>
                <p>Steps:</p>
                <ul>

                    <li style="">
                    1: first step ( success )
                    </li>

                    <li style="">
                    2: second step ( success )
                    (
                        <a href="http://localhost:8080/#/builders/80/builds/1/steps/2/logs/stdio">&lt;stdio&gt;</a>
                        <a href="http://localhost:8080/#/builders/80/builds/1/steps/2/logs/stderr">&lt;stderr&gt;</a>
                    )
                    </li>

                    <li style="">
                    3: third step ( success )
                    (
                        <a href="http://localhost:8080/#/builders/80/builds/1/steps/3/logs/stdio">&lt;stdio&gt;</a>
                    )
                    </li>

                </ul>"""),
            },
        )

    @defer.inlineCallbacks
    def test_inline_templates(self):
        formatter = message.MessageFormatter(
            template="URL: {{ build_url }} -- {{ summary }}", subject="subject"
        )
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS)
        self.assertEqual(
            res,
            {
                "type": "plain",
                "subject": "subject",
                "extra_info": None,
                "body": "URL: http://localhost:8080/#/builders/80/builds/1 -- Build succeeded!",
            },
        )

    @defer.inlineCallbacks
    def test_inline_templates_extra_info(self):
        formatter = message.MessageFormatter(
            template="URL: {{ build_url }} -- {{ summary }}",
            subject="subject",
            extra_info_cb=lambda ctx: {"key1", ctx["build"]["state_string"]},
        )
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS)
        self.assertEqual(
            res,
            {
                "type": "plain",
                "subject": "subject",
                "extra_info": {"key1", "test"},
                "body": "URL: http://localhost:8080/#/builders/80/builds/1 -- Build succeeded!",
            },
        )

    @defer.inlineCallbacks
    def test_message_failure(self):
        formatter = message.MessageFormatter()
        res = yield self.do_one_test(formatter, SUCCESS, FAILURE)
        self.assertIn(
            "A failed build has been detected on builder Builder1 while building", res['body']
        )

    @defer.inlineCallbacks
    def test_message_failure_change(self):
        formatter = message.MessageFormatter()
        res = yield self.do_one_test(formatter, SUCCESS, FAILURE, "change")
        self.assertIn(
            "A new failure has been detected on builder Builder1 while building", res['body']
        )

    @defer.inlineCallbacks
    def test_message_success_change(self):
        formatter = message.MessageFormatter()
        res = yield self.do_one_test(formatter, FAILURE, SUCCESS, "change")
        self.assertIn(
            "A restored build has been detected on builder Builder1 while building", res['body']
        )

    @defer.inlineCallbacks
    def test_message_success_nochange(self):
        formatter = message.MessageFormatter()
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS, "change")
        self.assertIn(
            "A passing build has been detected on builder Builder1 while building", res['body']
        )

    @defer.inlineCallbacks
    def test_message_failure_nochange(self):
        formatter = message.MessageFormatter()
        res = yield self.do_one_test(formatter, FAILURE, FAILURE, "change")
        self.assertIn(
            "A failed build has been detected on builder Builder1 while building", res['body']
        )


class TestMessageFormatterRenderable(MessageFormatterTestBase):
    @defer.inlineCallbacks
    def test_basic(self):
        template = Interpolate('templ_%(prop:workername)s/%(prop:reason)s')
        subject = Interpolate('subj_%(prop:workername)s/%(prop:reason)s')
        formatter = message.MessageFormatterRenderable(template, subject)
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS)
        self.assertEqual(
            res,
            {
                'body': 'templ_wrkr/because',
                'type': 'plain',
                'subject': 'subj_wrkr/because',
                "extra_info": None,
            },
        )


class TestMessageFormatterFunction(MessageFormatterTestBase):
    @defer.inlineCallbacks
    def test_basic(self):
        function = mock.Mock(side_effect=lambda x: {'key': 'value'})
        formatter = message.MessageFormatterFunction(function, 'json')
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS)

        function.assert_called_with({
            'build': BuildDictLookAlike(
                extra_keys=['prev_build'],
                expected_missing_keys=['parentbuilder', 'buildrequest', 'parentbuild'],
            )
        })
        self.assertEqual(
            res,
            {
                'body': {'key': 'value'},
                'type': 'json',
                'subject': None,
                "extra_info": None,
            },
        )


class TestMessageFormatterFunctionRaw(MessageFormatterTestBase):
    @defer.inlineCallbacks
    def test_basic(self):
        function = mock.Mock(
            side_effect=lambda master, ctx: {
                "body": {"key": "value"},
                "type": "json",
                "subject": "sub1",
                "extra_info": {"key": {"kk": "vv"}},
            }
        )
        formatter = message.MessageFormatterFunctionRaw(function)
        res = yield self.do_one_test(formatter, SUCCESS, SUCCESS)

        self.assertEqual(
            res,
            {
                "body": {"key": "value"},
                "type": "json",
                "subject": "sub1",
                "extra_info": {"key": {"kk": "vv"}},
            },
        )

    @defer.inlineCallbacks
    def test_basic_buildset(self):
        function = mock.Mock(
            side_effect=lambda master, ctx: {
                "body": {"key": "value"},
                "type": "json",
                "subject": "sub1",
                "extra_info": {"key": {"kk": "vv"}},
            }
        )
        formatter = message.MessageFormatterFunctionRaw(function)
        res = yield self.do_one_test_buildset(formatter, SUCCESS, SUCCESS)

        self.assertEqual(
            res,
            {
                "body": {"key": "value"},
                "type": "json",
                "subject": "sub1",
                "extra_info": {"key": {"kk": "vv"}},
            },
        )


class TestMessageFormatterMissingWorker(MessageFormatterTestBase):
    @defer.inlineCallbacks
    def test_basic(self):
        formatter = message.MessageFormatterMissingWorker()
        self.setup_db(SUCCESS, SUCCESS)
        workers = yield self.master.data.get(('workers',))
        worker = workers[0]
        worker['notify'] = ['e@mail']
        worker['last_connection'] = ['yesterday']
        res = yield formatter.formatMessageForMissingWorker(self.master, worker)
        text = res['body']
        self.assertIn("worker named wrkr went away", text)

    def test_unknown_template_type_for_default_message(self):
        with self.assertRaises(config.ConfigErrors):
            message.MessageFormatterMissingWorker(template_type='unknown')
