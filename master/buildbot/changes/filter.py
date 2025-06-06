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
from typing import Any
from typing import Callable
from typing import ClassVar

from buildbot.util import ComparableMixin
from buildbot.util import NotABranch
from buildbot.util.ssfilter import _create_branch_filters
from buildbot.util.ssfilter import _create_filters
from buildbot.util.ssfilter import _create_property_filters

if TYPE_CHECKING:
    from collections.abc import Sequence
    from re import Pattern


class ChangeFilter(ComparableMixin):
    # NOTE: If users use a filter_fn, we have no way to determine whether it has
    # changed at reconfig, so the scheduler will always be restarted.  That's as
    # good as Python can do.
    compare_attrs: ClassVar[Sequence[str]] = (
        'filter_fn',
        'filters',
        'property_filters',
        'project_fn',
        'repository_fn',
        'branch_fn',
        'category_fn',
        'codebase_fn',
    )

    def __init__(
        self,
        filter_fn: Callable[[Any], bool] | None = None,  # gets a Change object, returns boolean
        project: str | list[str] | None = None,
        project_not_eq: str | list[str] | None = None,
        project_re: str | Pattern | None = None,
        project_not_re: str | Pattern | None = None,
        project_fn: Callable[[str], bool] | None = None,
        repository: str | list[str] | None = None,
        repository_not_eq: str | list[str] | None = None,
        repository_re: str | Pattern | None = None,
        repository_not_re: str | Pattern | None = None,
        repository_fn: Callable[[str], bool] | None = None,
        branch: Any = NotABranch,
        branch_not_eq: Any = NotABranch,
        branch_re: str | Pattern | None = None,
        branch_not_re: str | Pattern | None = None,
        branch_fn: Callable[[str], bool] | None = None,
        category: str | list[str] | None = None,
        category_not_eq: str | list[str] | None = None,
        category_re: str | Pattern | None = None,
        category_not_re: str | Pattern | None = None,
        category_fn: Callable[[str], bool] | None = None,
        codebase: str | list[str] | None = None,
        codebase_not_eq: str | list[str] | None = None,
        codebase_re: str | Pattern | None = None,
        codebase_not_re: str | Pattern | None = None,
        codebase_fn: Callable[[str], bool] | None = None,
        property_eq: dict[str, Any] | None = None,
        property_not_eq: dict[str, Any] | None = None,
        property_re: dict[str, str | Pattern] | None = None,
        property_not_re: dict[str, str | Pattern] | None = None,
    ) -> None:
        self.filter_fn = filter_fn
        self.project_fn = project_fn
        self.repository_fn = repository_fn
        self.branch_fn = branch_fn
        self.category_fn = category_fn
        self.codebase_fn = codebase_fn

        self.filters = _create_filters(
            project, project_not_eq, project_re, project_not_re, 'project'
        )
        self.filters += _create_filters(
            repository,
            repository_not_eq,
            repository_re,
            repository_not_re,
            'repository',
        )
        self.filters += _create_branch_filters(
            branch,
            branch_not_eq,
            branch_re,
            branch_not_re,
            'branch',
        )
        self.filters += _create_filters(
            category,
            category_not_eq,
            category_re,
            category_not_re,
            'category',
        )
        self.filters += _create_filters(
            codebase,
            codebase_not_eq,
            codebase_re,
            codebase_not_re,
            'codebase',
        )
        self.property_filters = _create_property_filters(
            property_eq, property_not_eq, property_re, property_not_re, 'property'
        )

    def filter_change(self, change: Any) -> bool:
        if self.filter_fn is not None and not self.filter_fn(change):
            return False
        if self.project_fn is not None and not self.project_fn(change.project):
            return False
        if self.codebase_fn is not None and not self.codebase_fn(change.codebase):
            return False
        if self.repository_fn is not None and not self.repository_fn(change.repository):
            return False
        if self.category_fn is not None and not self.category_fn(change.category):
            return False
        if self.branch_fn is not None and not self.branch_fn(change.branch):
            return False

        for filter in self.filters:
            value = getattr(change, filter.prop, '')
            if not filter.is_matched(value):
                return False
        for filter in self.property_filters:
            value = change.properties.getProperty(filter.prop, '')
            if not filter.is_matched(value):
                return False
        return True

    def _get_repr_filters(self) -> list[str]:
        filters = []
        if self.filter_fn is not None:
            filters.append(f'{self.filter_fn.__name__}()')
        if self.project_fn is not None:
            filters.append(f'{self.project_fn.__name__}(project)')
        if self.codebase_fn is not None:
            filters.append(f'{self.codebase_fn.__name__}(codebase)')
        if self.repository_fn is not None:
            filters.append(f'{self.repository_fn.__name__}(repository)')
        if self.category_fn is not None:
            filters.append(f'{self.category_fn.__name__}(category)')
        if self.branch_fn is not None:
            filters.append(f'{self.branch_fn.__name__}(branch)')
        filters += [filter.describe() for filter in self.filters]
        filters += [filter.describe() for filter in self.property_filters]
        return filters

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} on {' and '.join(self._get_repr_filters())}>"

    @staticmethod
    def fromSchedulerConstructorArgs(
        change_filter: ChangeFilter | None = None,
        branch: Any = NotABranch,
        categories: str | list[str] | None = None,
    ) -> ChangeFilter | None:
        """
        Static method to create a filter based on constructor args
        change_filter, branch, and categories; use default values @code{None},
        @code{NotABranch}, and @code{None}, respectively.  These arguments are
        interpreted as documented for the
        L{buildbot.schedulers.basic.Scheduler} class.

        @returns: L{ChangeFilter} instance or None for not filtering
        """

        # use a change_filter, if given one
        if change_filter:
            if branch is not NotABranch or categories is not None:
                raise RuntimeError("cannot specify both change_filter and branch or categories")
            return change_filter
        elif branch is not NotABranch or categories:
            # build a change filter from the deprecated category and branch
            # args
            cfargs = {}
            if branch is not NotABranch:
                cfargs['branch'] = branch
            if categories:
                cfargs['category'] = categories
            return ChangeFilter(**cfargs)
        else:
            return None
