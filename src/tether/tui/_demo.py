"""Composition and resource ownership for the local terminal demo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.widgets import Input

from tether.cli.demo import LocalModelOptions, ReferenceModelProfile, open_demo, resolve_profile
from tether.client import Actor, ClientProtocol

from ._presentation import SessionApp


class DemoApp(SessionApp):
    TITLE = "Tether · offline demo"

    def __init__(
        self,
        root: Path | None = None,
        *,
        client: ClientProtocol | None = None,
        session_id: str | None = None,
        volatile: bool = False,
        model_config: Path | None = None,
        local_options: LocalModelOptions | None = None,
    ) -> None:
        super().__init__(client, session_id=session_id)
        self.root = root
        self.volatile = volatile
        self.model_config = model_config
        self.local_options = local_options
        self.actor: Actor | None = None
        self._demo_context: Any = None

    async def on_mount(self) -> None:
        if self.client is not None:
            self.title = "Tether · client"
            await self._connect_client(self._initial_session_id)
            return
        if self.root is None:
            raise ValueError("demo_root_required")
        profile = await resolve_profile(self.model_config, self.local_options)
        if profile is not None and profile.available_models:
            from .model_picker import ModelPicker

            async def selected(name: str | None) -> None:
                if name is None:
                    self.exit()
                    return
                await self._connect(
                    profile.model_copy(update={"model_name": name, "available_models": ()})
                )

            self.push_screen(ModelPicker(profile.available_models), selected)
            return
        await self._connect(profile)

    async def _connect(self, profile: ReferenceModelProfile | None) -> None:
        if self.root is None:
            raise ValueError("demo_root_required")
        if profile is not None:
            self.title = "Tether · " + profile.id
            if self.local_options is not None:
                self.sub_title = profile.model_name
            self.query_one("#message", Input).placeholder = "Send a message"
        self._demo_context = open_demo(self.root, volatile=self.volatile, profile=profile)
        app, actor, _ = await self._demo_context.__aenter__()
        client = app.client(actor)
        self.client, self.actor = client, actor
        session_ref = profile.session_ref if profile is not None else "demo"
        demo = await client.find(session_ref)
        if demo is None:
            demo = await client.create(
                ref=session_ref,
                title="Model · " + profile.id if profile is not None else "Offline demo",
                settings={"approval": "manual"},
            )
        self.session_id = demo.id
        notice = None
        if self.local_options is not None:
            notice = (
                f"Connected model: {profile.model_name if profile else 'local'}. "
                f"Context budget: {profile.context_window_tokens if profile else 0} "
                f"({profile.context_source if profile else 'starter'}); approximate byte estimate. "
                "Tools use the selected workspace and require approval."
            )
        await self._connected(notice=notice)

    async def on_unmount(self) -> None:
        try:
            await self._stop_follower()
        finally:
            if self._demo_context is not None:
                await self._demo_context.__aexit__(None, None, None)


def run(
    root: Path,
    *,
    volatile: bool = False,
    model_config: Path | None = None,
    local_options: LocalModelOptions | None = None,
) -> None:
    DemoApp(root, volatile=volatile, model_config=model_config, local_options=local_options).run()
