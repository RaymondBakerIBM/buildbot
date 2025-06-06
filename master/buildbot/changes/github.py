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

from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import ClassVar
from typing import cast

from twisted.internet import defer
from twisted.logger import Logger

from buildbot import config
from buildbot.changes import base
from buildbot.util import bytes2unicode
from buildbot.util import datetime2epoch
from buildbot.util import httpclientservice
from buildbot.util.pullrequest import PullRequestMixin
from buildbot.util.state import StateMixin

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buildbot.util.twisted import InlineCallbacksType

log = Logger()

HOSTED_BASE_URL = "https://api.github.com"
link_urls = {"https": "clone_url", "svn": "svn_url", "git": "git_url", "ssh": "ssh_url"}


class GitHubPullrequestPoller(base.ReconfigurablePollingChangeSource, StateMixin, PullRequestMixin):
    compare_attrs: ClassVar[Sequence[str]] = (
        "owner",
        "repo",
        "token",
        "branches",
        "pollInterval",
        "category",
        "project",
        "pollAtLaunch",
        "name",
    )
    db_class_name = 'GitHubPullrequestPoller'
    property_basename = "github"

    def __init__(self, owner: str, repo: str, **kwargs: Any) -> None:
        name = kwargs.get("name")
        if not name:
            kwargs["name"] = "GitHubPullrequestPoller:" + owner + "/" + repo
        super().__init__(owner, repo, **kwargs)

    def checkConfig(  # type: ignore[override]
        self,
        owner: str,
        repo: str,
        branches: list[str] | None = None,
        category: str | Callable[[Any], Any] = 'pull',
        project: str | None = None,
        baseURL: str | None = None,
        pullrequest_filter: bool | Callable[[Any], bool] = True,
        token: str | None = None,
        magic_link: bool = False,
        repository_type: str = "https",
        github_property_whitelist: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        if repository_type not in ["https", "svn", "git", "ssh"]:
            config.error("repository_type must be one of {https, svn, git, ssh}")
        super().checkConfig(name=self.name, **kwargs)

    @defer.inlineCallbacks
    def reconfigService(  # type: ignore[override]
        self,
        owner: str,
        repo: str,
        branches: list[str] | None = None,
        pollInterval: int = 10 * 60,
        category: str | Callable[[Any], Any] | None = None,
        project: str | None = None,
        baseURL: str | None = None,
        pullrequest_filter: bool | Callable[[Any], bool] = True,
        token: str | None = None,
        pollAtLaunch: bool = False,
        magic_link: bool = False,
        repository_type: str = "https",
        github_property_whitelist: list[str] | None = None,
        **kwargs: Any,
    ) -> InlineCallbacksType[None]:
        yield super().reconfigService(name=self.name, **kwargs)

        if baseURL is None:
            baseURL = HOSTED_BASE_URL
        if baseURL.endswith('/'):
            baseURL = baseURL[:-1]

        http_headers = {'User-Agent': 'Buildbot'}
        if token is not None:
            token = yield self.renderSecrets(token)
            # token is not None here, but mypy doesn't know that
            http_headers.update({'Authorization': 'token ' + cast(str, token)})

        if github_property_whitelist is None:
            github_property_whitelist = []

        self._http = yield httpclientservice.HTTPSession(
            self.master.httpservice, baseURL, headers=http_headers
        )

        self.token = token
        self.owner = owner
        self.repo = repo
        self.branches = branches
        self.pollInterval = pollInterval
        self.pollAtLaunch = pollAtLaunch
        self.repository_type = link_urls[repository_type]
        self.magic_link = magic_link
        self.external_property_whitelist = github_property_whitelist

        if callable(pullrequest_filter):
            self.pullrequest_filter = pullrequest_filter
        else:
            self.pullrequest_filter = lambda _: pullrequest_filter

        self.category = category if callable(category) else bytes2unicode(category)
        self.project = bytes2unicode(project)

    def describe(self) -> str:
        return f"GitHubPullrequestPoller watching the GitHub repository {self.owner}/{self.repo}"

    @defer.inlineCallbacks
    def _getPullInformation(self, pull_number: int) -> InlineCallbacksType[dict[str, Any]]:
        result = yield self._http.get(
            '/'.join(['/repos', self.owner, self.repo, 'pulls', str(pull_number)])
        )
        my_json = yield result.json()
        return my_json

    @defer.inlineCallbacks
    def _getPulls(self) -> InlineCallbacksType[list[dict[str, Any]]]:
        log.debug(
            "GitHubPullrequestPoller: polling "
            f"GitHub repository {self.owner}/{self.repo}, branches: {self.branches}"
        )
        result = yield self._http.get('/'.join(['/repos', self.owner, self.repo, 'pulls']))
        my_json = yield result.json()
        if result.code != 200:
            message = my_json.get('message', 'unknown')
            log.error(
                f"GitHubPullrequestPoller error {result.code} '{message}' "
                f"while loading {result.url}"
            )
            return []
        return my_json

    @defer.inlineCallbacks
    def _getFiles(self, prnumber: int) -> InlineCallbacksType[list[str]]:
        result = yield self._http.get(
            "/".join(['/repos', self.owner, self.repo, 'pulls', str(prnumber), 'files'])
        )
        my_json = yield result.json()

        return [f["filename"] for f in my_json]

    @defer.inlineCallbacks
    def _getCommitters(self, prnumber: int) -> InlineCallbacksType[list[list[str]]]:
        result = yield self._http.get(
            "/".join(['/repos', self.owner, self.repo, 'pulls', str(prnumber), 'commits'])
        )
        my_json = yield result.json()

        return [
            [c["commit"]["committer"]["name"], c["commit"]["committer"]["email"]] for c in my_json
        ]

    @defer.inlineCallbacks
    def _getAuthors(self, prnumber: int) -> InlineCallbacksType[list[list[str]]]:
        result = yield self._http.get(
            "/".join(['/repos', self.owner, self.repo, 'pulls', str(prnumber), 'commits'])
        )
        my_json = yield result.json()

        return [[a["commit"]["author"]["name"], a["commit"]["author"]["email"]] for a in my_json]

    @defer.inlineCallbacks
    def _getCurrentRev(self, prnumber: int) -> InlineCallbacksType[str | None]:
        # Get currently assigned revision of PR number

        result = yield self._getStateObjectId()
        rev = yield self.master.db.state.getState(result, f'pull_request{prnumber}', None)
        return rev

    @defer.inlineCallbacks
    def _setCurrentRev(self, prnumber: int, rev: str) -> InlineCallbacksType[None]:
        # Set the updated revision for PR number.

        result = yield self._getStateObjectId()
        yield self.master.db.state.setState(result, f'pull_request{prnumber}', rev)

    @defer.inlineCallbacks
    def _getStateObjectId(self) -> InlineCallbacksType[int]:
        # Return a deferred for object id in state db.
        result = yield self.master.db.state.getObjectId(
            f'{self.owner}/{self.repo}', self.db_class_name
        )
        return result

    @defer.inlineCallbacks
    def _processChanges(self, github_result: list[dict[str, Any]]) -> InlineCallbacksType[None]:
        for pr in github_result:
            # Track PRs for specified branches
            base_branch = pr['base']['ref']
            prnumber = pr['number']
            revision = pr['head']['sha']

            # Check to see if the branch is set or matches
            if self.branches is not None and base_branch not in self.branches:
                continue
            if self.pullrequest_filter is not None and not self.pullrequest_filter(pr):
                continue
            current = yield self._getCurrentRev(prnumber)
            if not current or current[0:12] != revision[0:12]:
                # Access title, repo, html link, and comments
                pr = yield self._getPullInformation(prnumber)
                title = pr['title']
                if self.magic_link:
                    branch = f'refs/pull/{prnumber}/merge'
                    repo = pr['base']['repo'][self.repository_type]
                else:
                    branch = pr['head']['ref']
                    repo = pr['head']['repo'][self.repository_type]
                revlink = pr['html_url']
                comments = pr['body']
                updated = datetime.strptime(pr['updated_at'], '%Y-%m-%dT%H:%M:%SZ')
                # update database
                yield self._setCurrentRev(prnumber, revision)

                project = self.project
                if project is None:
                    project = pr['base']['repo']['full_name']
                commits = pr['commits']

                dl: defer.Deferred[Any] = defer.DeferredList(
                    [
                        self._getAuthors(prnumber),
                        self._getCommitters(prnumber),
                        self._getFiles(prnumber),
                    ],
                    consumeErrors=True,
                )

                results = yield dl
                failures = [r[1] for r in results if not r[0]]
                if failures:
                    for failure in failures:
                        log.error(
                            "while processing changes for "
                            f"Pullrequest {prnumber} revision {revision}: {failure}"
                        )
                    # Fail on the first error!
                    failures[0].raiseException()
                [authors, committers, files] = [r[1] for r in results]

                author = authors[0][0] + " <" + authors[0][1] + ">"

                committer = committers[0][0] + " <" + committers[0][1] + ">"

                # emit the change
                yield self.master.data.updates.addChange(
                    author=author,
                    committer=committer,
                    revision=bytes2unicode(revision),
                    revlink=bytes2unicode(revlink),
                    comments=f"GitHub Pull Request #{prnumber} "
                    f"({commits} commit{'s' if commits > 0 else ''})\n{title}\n{comments}",
                    when_timestamp=datetime2epoch(updated),
                    branch=bytes2unicode(branch),
                    category=self.category,
                    project=project,
                    repository=bytes2unicode(repo),
                    files=files,
                    properties={
                        'pullrequesturl': revlink,
                        **self.extractProperties(pr),
                    },
                    src='git',
                )

    @defer.inlineCallbacks
    def poll(self) -> InlineCallbacksType[None]:  # type: ignore[override]
        result = yield self._getPulls()
        yield self._processChanges(result)
