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

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import ClassVar

from twisted.internet import defer
from twisted.python import log

from buildbot import config
from buildbot import util
from buildbot.process.results import CANCELLED
from buildbot.process.results import EXCEPTION
from buildbot.process.results import FAILURE
from buildbot.process.results import SUCCESS
from buildbot.process.results import WARNINGS
from buildbot.process.results import statusToString
from buildbot.warnings import warn_deprecated

if TYPE_CHECKING:
    from collections.abc import Sequence


class BuildStatusGeneratorMixin(util.ComparableMixin):
    possible_modes = (
        "change",
        "failing",
        "passing",
        "problem",
        "warnings",
        "exception",
        "cancelled",
    )

    compare_attrs: ClassVar[Sequence[str]] = [
        'mode',
        'tags',
        'builders',
        'schedulers',
        'branches',
        'subject',
        'add_logs',
        'add_patch',
    ]

    def __init__(self, mode, tags, builders, schedulers, branches, subject, add_logs, add_patch):
        self.mode = self._compute_shortcut_modes(mode)

        if add_logs is not None:
            warn_deprecated(
                '4.1.0',
                (
                    f'{self.__class__.__name__} argument add_logs have been deprecated. '
                    'Please use want_logs_content of the passed message formatter.'
                ),
            )

        self.tags = tags
        self.builders = builders
        self.schedulers = schedulers
        self.branches = branches
        self.subject = subject
        self.add_logs = add_logs
        self.add_patch = add_patch

    def check(self):
        self._verify_build_generator_mode(self.mode)

        if self.subject is not None and '\n' in self.subject:
            config.error('Newlines are not allowed in message subjects')

        list_or_none_params = [
            ('tags', self.tags),
            ('builders', self.builders),
            ('schedulers', self.schedulers),
            ('branches', self.branches),
        ]
        for name, param in list_or_none_params:
            self._verify_list_or_none_param(name, param)

        # you should either limit on builders or tags, not both
        if self.builders is not None and self.tags is not None:
            config.error("Please specify only builders or tags to include - not both.")

    def generate_name(self):
        name = self.__class__.__name__
        if self.tags is not None:
            name += "_tags_" + "+".join(self.tags)
        if self.builders is not None:
            name += "_builders_" + "+".join(self.builders)
        if self.schedulers is not None:
            name += "_schedulers_" + "+".join(self.schedulers)
        if self.branches is not None:
            name += "_branches_" + "+".join(self.branches)
        name += "_".join(self.mode)
        return name

    def is_message_needed_by_props(self, build):
        builder = build['builder']
        scheduler = build['properties'].get('scheduler', [None])[0]
        branch = build['properties'].get('branch', [None])[0]

        if self.builders is not None and builder['name'] not in self.builders:
            return False
        if self.schedulers is not None and scheduler not in self.schedulers:
            return False
        if self.branches is not None and branch not in self.branches:
            return False
        if self.tags is not None and not self._matches_any_tag(builder['tags']):
            return False
        return True

    def is_message_needed_by_results(self, build):
        results = build['results']
        if "change" in self.mode:
            prev = build['prev_build']
            if prev and prev['results'] != results:
                return True
        if "failing" in self.mode and results == FAILURE:
            return True
        if "passing" in self.mode and results == SUCCESS:
            return True
        if "problem" in self.mode and results == FAILURE:
            prev = build['prev_build']
            if prev and prev['results'] != FAILURE:
                return True
        if "warnings" in self.mode and results == WARNINGS:
            return True
        if "exception" in self.mode and results == EXCEPTION:
            return True
        if "cancelled" in self.mode and results == CANCELLED:
            return True

        return False

    def _merge_msgtype(self, msgtype, new_msgtype):
        if new_msgtype is None:
            return msgtype, False
        if msgtype is None:
            return new_msgtype, True
        if msgtype != new_msgtype:
            log.msg(
                f'{self}: Incompatible message types for multiple builds '
                f'({msgtype} and {new_msgtype}). Ignoring'
            )
            return msgtype, False

        return msgtype, True

    def _merge_subject(self, subject, new_subject):
        if subject is None and new_subject is not None:
            return new_subject
        return subject

    def _merge_body(self, body, new_body):
        if body is None:
            return new_body, True
        if new_body is None:
            return body, True

        if isinstance(body, str) and isinstance(new_body, str):
            return body + new_body, True

        if isinstance(body, list) and isinstance(new_body, list):
            return body + new_body, True

        log.msg(
            f'{self}: Incompatible message body types for multiple builds '
            f'({type(body)} and {type(new_body)}). Ignoring'
        )
        return body, False

    def _merge_extra_info(self, info, new_info):
        if info is None:
            return new_info, True
        if new_info is None:
            return info, True

        for key, new_value in new_info.items():
            if key not in info:
                info[key] = new_value
                continue

            value = info[key]
            for vkey, vvalue in new_value.items():
                if vkey not in value:
                    value[vkey] = vvalue

        return info, True

    def _get_patches_for_build(self, build):
        if not self.add_patch:
            return []

        ss_list = build['buildset']['sourcestamps']

        return [ss['patch'] for ss in ss_list if 'patch' in ss and ss['patch'] is not None]

    @defer.inlineCallbacks
    def build_message(self, formatter, master, reporter, build):
        patches = self._get_patches_for_build(build)

        logs = self._get_logs_for_build(build)

        users = yield reporter.getResponsibleUsersForBuild(master, build['buildid'])

        buildmsg = yield formatter.format_message_for_build(
            master, build, is_buildset=False, mode=self.mode, users=users
        )

        results = build['results']

        subject = buildmsg['subject']
        if subject is None and self.subject is not None:
            subject = self.subject % {
                'result': statusToString(results),
                'projectName': master.config.title,
                'title': master.config.title,
                'builder': build['builder']['name'],
            }

        return {
            'body': buildmsg['body'],
            'subject': subject,
            'type': buildmsg['type'],
            'results': results,
            'builds': [build],
            "buildset": build["buildset"],
            'users': list(users),
            'patches': patches,
            'logs': logs,
            "extra_info": buildmsg["extra_info"],
        }

    def _get_logs_for_build(self, build):
        if 'steps' not in build:
            return []

        all_logs = []
        for step in build['steps']:
            if 'logs' not in step:
                continue
            for l in step['logs']:
                if 'content' in l:
                    all_logs.append(l)
        return all_logs

    def _verify_build_generator_mode(self, mode):
        for m in self._compute_shortcut_modes(mode):
            if m not in self.possible_modes:
                if m == "all":
                    config.error(
                        "mode 'all' is not valid in an iterator and must be "
                        "passed in as a separate string"
                    )
                else:
                    config.error(f"mode {m} is not a valid mode")

    def _verify_list_or_none_param(self, name, param):
        if param is not None and not isinstance(param, list):
            config.error(f"{name} must be a list or None")

    def _compute_shortcut_modes(self, mode):
        if isinstance(mode, str):
            if mode == "all":
                mode = ("failing", "passing", "warnings", "exception", "cancelled")
            elif mode == "warnings":
                mode = ("failing", "warnings")
            else:
                mode = (mode,)
        return mode

    def _matches_any_tag(self, tags):
        return self.tags and any(tag for tag in self.tags if tag in tags)
