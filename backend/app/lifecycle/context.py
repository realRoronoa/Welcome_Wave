"""What a lifecycle service works with: the container (stores, index, agents' dependencies), the caller and the clock."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from app.lifecycle.clock import Clock, for_org, program_day
from app.ports import Onboarding, OnboardingProgram, User


@dataclass
class Ctx:
    c: object  # app.container.Container
    clock: Clock
    user: User | None = None
    _names: dict = field(default_factory=dict)

    @classmethod
    def of(cls, c, user: User | None = None, org_id: str | None = None) -> "Ctx":
        return cls(c, for_org(c, org_id or user.org_id), user)

    @property
    def lc(self):
        return self.c.lifecycle

    @property
    def store(self):
        return self.c.store

    @property
    def index(self):
        return self.c.index

    @property
    def deps(self):
        return self.c.deps

    @property
    def settings(self):
        return self.c.settings

    def person(self, user_id: str | None) -> dict | None:
        """{id, name} for a user id, read once per request."""
        if not user_id:
            return None
        if user_id not in self._names:
            u = self.store.get_user(user_id)
            self._names[user_id] = {"id": u.id, "name": u.name} if u else {"id": user_id, "name": None}
        return self._names[user_id]


@dataclass
class HireCtx:
    """One onboarding seen through one request: every read is made at most once, however many fields use it."""
    ctx: Ctx
    ob: Onboarding
    program: OnboardingProgram

    @property
    def id(self) -> str:
        return self.ob.id

    @cached_property
    def hire(self) -> User | None:
        return self.ctx.store.get_user(self.ob.new_hire_id)

    @cached_property
    def day(self) -> int:
        return program_day(self.ob.start_date, self.ctx.clock.today())

    @cached_property
    def tasks(self):
        return self.ctx.store.tasks(onboarding_id=self.ob.id)

    @cached_property
    def milestones(self):
        return self.ctx.lc.milestones(self.ob.id)

    @cached_property
    def access(self):
        return self.ctx.lc.access_items(self.ob.id)

    @cached_property
    def acknowledgements(self):
        return self.ctx.lc.acknowledgements(self.ob.id)

    @cached_property
    def environment_checks(self):
        return self.ctx.lc.environment_checks(self.ob.id)

    @cached_property
    def checkins(self):
        return self.ctx.lc.checkins(self.ob.id)

    @cached_property
    def chats(self) -> list[dict]:
        return self.ctx.store.chat_history(self.ob.project_id, self.ob.new_hire_id, limit=200)

    @cached_property
    def kt_links(self):
        return self.ctx.store.kt_links(self.ob.project_id)

    def forget(self, *names: str) -> None:
        """Drop cached reads after a write, so the response shows it."""
        for n in names:
            self.__dict__.pop(n, None)
