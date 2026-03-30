#!/usr/bin/env python3
"""Wwise 留声机（Music Playlist）批量搭建工具（带 UI）。"""

from __future__ import annotations

import json
import queue
import re
import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

try:
    from websocket import create_connection
except Exception as exc:  # noqa: BLE001
    raise SystemExit(
        "缺少依赖 websocket-client。请先执行: pip install websocket-client"
    ) from exc


FORMAL_SWITCH_ID = "{67EF15FE-7263-44A2-B790-4E113AE75FA9}"
PLAYLIST_ENABLED_STATE_ID = "{C7B549E2-9323-4DC4-9231-F1586DE51A04}"

FORMAL_SWITCH_PATH = r"\Interactive Music Hierarchy\X6_Music\X6_Music\BGM_Music_Playlist\BGM_Music_Playlist_Formal"
PREPLAY_SWITCH_PATH = r"\Interactive Music Hierarchy\X6_Music\X6_Music\BGM_Music_Playlist\BGM_Music_Playlist_PrePlay"

FORMAL_EVENT_FOLDER_PATH = r"\Events\bgm\bgm\BGM_Playlist\Formal"
PREPLAY_EVENT_FOLDER_PATH = r"\Events\bgm\bgm\BGM_Playlist\Preplay"

FORMAL_NOTES = "播放留声机对应音乐"
PREPLAY_NOTES = "播放留声机对应试听"


class WaapiError(RuntimeError):
    pass


class WaapiClient:
    """Minimal WAMP client for WAAPI over WebSocket."""

    def __init__(self, url: str = "ws://127.0.0.1:8080/waapi") -> None:
        self.url = url
        self.ws = None
        self._request_id = 1

    def connect(self) -> None:
        self.ws = create_connection(self.url, timeout=10)
        self.ws.send(json.dumps([1, "realm1", {"roles": {"caller": {}}}]))
        msg = json.loads(self.ws.recv())
        if not msg or msg[0] != 2:
            raise WaapiError(f"WAAPI 握手失败，收到: {msg}")

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            self.ws = None

    def call(self, uri: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.ws is None:
            raise WaapiError("WAAPI 未连接")

        request_id = self._request_id
        self._request_id += 1

        payload = [48, request_id, {}, uri, args or {}, {}]
        self.ws.send(json.dumps(payload))

        while True:
            msg = json.loads(self.ws.recv())
            msg_type = msg[0]

            if msg_type == 50 and msg[1] == request_id:
                return msg[3] if len(msg) >= 4 else {}

            if msg_type == 8 and msg[2] == request_id:
                err = msg[4] if len(msg) > 4 else "unknown_error"
                details = msg[5] if len(msg) > 5 else {}
                raise WaapiError(f"WAAPI 调用失败: {uri}, error={err}, details={details}")


@dataclass
class MusicTargets:
    formal_segments: List[Dict[str, str]]
    preplay_playlists: List[Dict[str, str]]


class PlaylistAutomator:
    def __init__(self, client: WaapiClient, log: Callable[[str], None]) -> None:
        self.client = client
        self.log = log

    def get_object_by_path(self, path: str, returns: Iterable[str]) -> Optional[Dict[str, Any]]:
        result = self.client.call(
            "ak.wwise.core.object.get",
            {
                "from": {"path": [path]},
                "options": {"return": list(returns)},
            },
        )
        rows = result.get("return", [])
        return rows[0] if rows else None

    def get_children(self, *, obj_id: Optional[str] = None, path: Optional[str] = None, returns: Iterable[str] = ("id", "name", "type")) -> List[Dict[str, Any]]:
        if not obj_id and not path:
            return []

        source: Dict[str, Any] = {"id": [obj_id]} if obj_id else {"path": [path]}
        result = self.client.call(
            "ak.wwise.core.object.get",
            {
                "from": source,
                "transform": [{"select": ["children"]}],
                "options": {"return": list(returns)},
            },
        )
        return result.get("return", [])

    def create_object(
        self,
        parent: str,
        obj_type: str,
        name: str,
        on_conflict: str = "merge",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "parent": parent,
            "type": obj_type,
            "name": name,
            "onNameConflict": on_conflict,
        }
        if extra:
            args.update(extra)
        return self.client.call("ak.wwise.core.object.create", args)

    def set_objects(self, objects: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self.client.call("ak.wwise.core.object.set", {"objects": objects})

    def find_target_workunits(self, numbers: List[str]) -> Dict[str, MusicTargets]:
        formal_children = self.get_children(path=FORMAL_SWITCH_PATH, returns=("id", "name", "type"))
        preplay_children = self.get_children(path=PREPLAY_SWITCH_PATH, returns=("id", "name", "type"))

        formal_map = {
            row["name"]: row
            for row in formal_children
            if row.get("type") == "WorkUnit" and row.get("name", "").startswith("BGM_Music_Playlist_Formal_")
        }
        preplay_map = {
            row["name"]: row
            for row in preplay_children
            if row.get("type") == "WorkUnit" and row.get("name", "").startswith("BGM_Music_Playlist_PrePlay_")
        }

        targets: Dict[str, MusicTargets] = {}
        for num in numbers:
            formal_name = f"BGM_Music_Playlist_Formal_{num}"
            preplay_name = f"BGM_Music_Playlist_PrePlay_{num}"

            formal_wu = formal_map.get(formal_name)
            preplay_wu = preplay_map.get(preplay_name)

            if not formal_wu:
                self.log(f"[警告] 未找到 Formal WorkUnit: {formal_name}")
            if not preplay_wu:
                self.log(f"[警告] 未找到 PrePlay WorkUnit: {preplay_name}")

            formal_segments = []
            if formal_wu:
                formal_segments = [
                    c
                    for c in self.get_children(obj_id=formal_wu["id"], returns=("id", "name", "type"))
                    if c.get("type") == "MusicSegment"
                ]

            preplay_playlists = []
            if preplay_wu:
                preplay_playlists = [
                    c
                    for c in self.get_children(obj_id=preplay_wu["id"], returns=("id", "name", "type"))
                    if c.get("type") == "MusicPlaylistContainer"
                ]

            targets[num] = MusicTargets(formal_segments=formal_segments, preplay_playlists=preplay_playlists)
        return targets

    def create_event_actions(self, event_id: str, stop_target: str, play_target: str) -> None:
        actions = [
            {"@ActionType": 2, "@Target": stop_target},
            {"@ActionType": 22, "@Target": PLAYLIST_ENABLED_STATE_ID},
            {"@ActionType": 1, "@Target": play_target},
        ]
        for action in actions:
            self.create_object(event_id, "Action", "", on_conflict="rename", extra=action)

    def set_notes_for_event_workunit(self, workunit_path: str, notes: str) -> None:
        events = [
            row
            for row in self.get_children(path=workunit_path, returns=("id", "type"))
            if row.get("type") == "Event"
        ]
        if not events:
            return
        payload = [{"object": event["id"], "notes": notes} for event in events]
        self.set_objects(payload)

    def build_events(self, numbers: List[str]) -> None:
        formal_folder = self.get_object_by_path(FORMAL_EVENT_FOLDER_PATH, ("id", "path"))
        preplay_folder = self.get_object_by_path(PREPLAY_EVENT_FOLDER_PATH, ("id", "path"))
        preplay_switch = self.get_object_by_path(PREPLAY_SWITCH_PATH, ("id", "path"))

        if not formal_folder or not preplay_folder or not preplay_switch:
            raise WaapiError("无法定位 Formal/Preplay Event 目录或 PrePlay SwitchContainer")

        targets = self.find_target_workunits(numbers)

        for num in numbers:
            self.log(f"[Event] 处理编号 {num}")
            t = targets[num]

            formal_wu_name = f"BGM_Music_Playlist_Formal_{num}"
            formal_wu = self.create_object(formal_folder["id"], "WorkUnit", formal_wu_name, on_conflict="merge")
            formal_wu_id = formal_wu.get("id") or self.get_object_by_path(
                f"{FORMAL_EVENT_FOLDER_PATH}\\{formal_wu_name}", ("id",)
            )["id"]

            for seg in t.formal_segments:
                event_name = f"Play_{seg['name']}"
                event = self.create_object(formal_wu_id, "Event", event_name, on_conflict="merge")
                event_id = event.get("id") or self.get_object_by_path(
                    f"{FORMAL_EVENT_FOLDER_PATH}\\{formal_wu_name}\\{event_name}", ("id",)
                )["id"]
                self.create_event_actions(event_id, FORMAL_SWITCH_ID, seg["id"])

            self.set_notes_for_event_workunit(
                f"{FORMAL_EVENT_FOLDER_PATH}\\{formal_wu_name}", FORMAL_NOTES
            )

            preplay_wu_name = f"BGM_Music_Playlist_PrePlay_{num}"
            preplay_wu = self.create_object(preplay_folder["id"], "WorkUnit", preplay_wu_name, on_conflict="merge")
            preplay_wu_id = preplay_wu.get("id") or self.get_object_by_path(
                f"{PREPLAY_EVENT_FOLDER_PATH}\\{preplay_wu_name}", ("id",)
            )["id"]

            for pl in t.preplay_playlists:
                event_name = pl["name"].replace(
                    "BGM_Music_Playlist_PrePlay_", "Play_BGM_Music_Playlist_Preplay_"
                )
                event = self.create_object(preplay_wu_id, "Event", event_name, on_conflict="merge")
                event_id = event.get("id") or self.get_object_by_path(
                    f"{PREPLAY_EVENT_FOLDER_PATH}\\{preplay_wu_name}\\{event_name}", ("id",)
                )["id"]
                self.create_event_actions(event_id, preplay_switch["id"], pl["id"])

            self.set_notes_for_event_workunit(
                f"{PREPLAY_EVENT_FOLDER_PATH}\\{preplay_wu_name}", PREPLAY_NOTES
            )

    def _set_playlist_loop_infinite(self, playlist_container_id: str, segment_id: str) -> None:
        """Best-effort for PrePlay playlist infinite loop configuration via WAAPI."""
        attempts = [
            {
                "object": playlist_container_id,
                "@Mode": 0,
                "@RandomAvoidRepeatingCount": 1,
                "@Playlist": {
                    "type": "PlaylistRoot",
                    "children": [
                        {
                            "type": "MusicSegment",
                            "object": segment_id,
                            "@LoopCount": 0,
                        }
                    ],
                },
            },
            {
                "object": playlist_container_id,
                "@Mode": 0,
                "@RandomAvoidRepeatingCount": 1,
                "@Playlist": {
                    "Children": [
                        {
                            "Object": segment_id,
                            "LoopCount": 0,
                        }
                    ]
                },
            },
        ]

        last_error: Optional[Exception] = None
        for payload in attempts:
            try:
                self.set_objects([payload])
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise WaapiError(f"配置 Playlist 无限循环失败: {last_error}")

    def build_music_containers(self, number: str, track_count: int) -> None:
        preplay_switch = self.get_object_by_path(PREPLAY_SWITCH_PATH, ("id",))
        if not preplay_switch:
            raise WaapiError("无法找到 PrePlay SwitchContainer")

        formal_wu_name = f"BGM_Music_Playlist_Formal_{number}"
        preplay_wu_name = f"BGM_Music_Playlist_PrePlay_{number}"

        formal_wu = self.create_object(FORMAL_SWITCH_ID, "WorkUnit", formal_wu_name, on_conflict="merge")
        formal_wu_id = formal_wu.get("id") or self.get_object_by_path(
            f"{FORMAL_SWITCH_PATH}\\{formal_wu_name}", ("id",)
        )["id"]

        preplay_wu = self.create_object(preplay_switch["id"], "WorkUnit", preplay_wu_name, on_conflict="merge")
        preplay_wu_id = preplay_wu.get("id") or self.get_object_by_path(
            f"{PREPLAY_SWITCH_PATH}\\{preplay_wu_name}", ("id",)
        )["id"]

        for idx in range(1, track_count + 1):
            suffix = f"{idx:02d}"

            formal_seg_name = f"BGM_Music_Playlist_Formal_{number}_M_{suffix}"
            self.create_object(formal_wu_id, "MusicSegment", formal_seg_name, on_conflict="merge")

            preplay_playlist_name = f"BGM_Music_Playlist_PrePlay_{number}_M_{suffix}"
            playlist = self.create_object(
                preplay_wu_id,
                "MusicPlaylistContainer",
                preplay_playlist_name,
                on_conflict="merge",
            )
            playlist_id = playlist.get("id") or self.get_object_by_path(
                f"{PREPLAY_SWITCH_PATH}\\{preplay_wu_name}\\{preplay_playlist_name}", ("id",)
            )["id"]

            preplay_seg_name = f"BGM_Music_Playlist_PrePlay_{number}_M{suffix}"
            segment = self.create_object(
                playlist_id,
                "MusicSegment",
                preplay_seg_name,
                on_conflict="merge",
            )
            segment_id = segment.get("id") or self.get_object_by_path(
                f"{PREPLAY_SWITCH_PATH}\\{preplay_wu_name}\\{preplay_playlist_name}\\{preplay_seg_name}", ("id",)
            )["id"]

            self._set_playlist_loop_infinite(playlist_id, segment_id)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Wwise 留声机 Playlist Automator")
        self.root.geometry("980x700")

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="WAAPI 地址").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value="ws://127.0.0.1:8080/waapi")
        ttk.Entry(frame, textvariable=self.url_var, width=52).grid(row=0, column=1, sticky="we", padx=8)

        ttk.Label(frame, text="编号（逗号/空格/换行分隔）").grid(row=1, column=0, sticky="nw", pady=(10, 0))
        self.numbers_input = scrolledtext.ScrolledText(frame, height=5, width=42)
        self.numbers_input.grid(row=1, column=1, sticky="we", pady=(10, 0), padx=8)

        ttk.Label(frame, text="曲目数量（用于容器创建）").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.track_count_var = tk.StringVar(value="40")
        ttk.Entry(frame, textvariable=self.track_count_var, width=12).grid(row=2, column=1, sticky="w", pady=(10, 0), padx=8)

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="w", pady=16)

        ttk.Button(buttons, text="仅创建音乐容器", command=self.on_build_music).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="仅创建 Event", command=self.on_build_events).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="一键执行（容器+Event）", command=self.on_build_all).pack(side=tk.LEFT)

        self.log_box = scrolledtext.ScrolledText(frame, height=22)
        self.log_box.grid(row=4, column=0, columnspan=2, sticky="nsew")

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.root.after(100, self._flush_log_queue)

    def _flush_log_queue(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_box.insert(tk.END, line + "\n")
            self.log_box.see(tk.END)
        self.root.after(100, self._flush_log_queue)

    def log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def parse_numbers(self) -> List[str]:
        raw = self.numbers_input.get("1.0", tk.END).strip()
        numbers = [s for s in re.split(r"[\s,，;；]+", raw) if s]
        if not numbers:
            raise ValueError("请至少输入 1 个编号")
        for num in numbers:
            if not re.fullmatch(r"\d+_\d+_\d+", num):
                raise ValueError(f"编号格式错误: {num}（应为 X_X_X）")
        return numbers

    def run_task(self, title: str, fn: Callable[[PlaylistAutomator, List[str]], None]) -> None:
        try:
            numbers = self.parse_numbers()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("输入错误", str(exc))
            return

        def worker() -> None:
            client = WaapiClient(self.url_var.get().strip())
            try:
                self.log(f"=== {title} 开始 ===")
                client.connect()
                automator = PlaylistAutomator(client, self.log)
                fn(automator, numbers)
                self.log(f"=== {title} 完成 ===")
            except Exception as exc:  # noqa: BLE001
                self.log(f"[错误] {exc}")
                self.log(traceback.format_exc())
            finally:
                client.close()

        threading.Thread(target=worker, daemon=True).start()

    def on_build_music(self) -> None:
        try:
            track_count = int(self.track_count_var.get().strip())
            if track_count <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "曲目数量必须是正整数")
            return

        def task(automator: PlaylistAutomator, numbers: List[str]) -> None:
            for number in numbers:
                self.log(f"[Music] 创建编号 {number}, 曲目数={track_count}")
                automator.build_music_containers(number, track_count)

        self.run_task("音乐容器批量搭建", task)

    def on_build_events(self) -> None:
        self.run_task("Event 批量搭建", lambda automator, numbers: automator.build_events(numbers))

    def on_build_all(self) -> None:
        try:
            track_count = int(self.track_count_var.get().strip())
            if track_count <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "曲目数量必须是正整数")
            return

        def task(automator: PlaylistAutomator, numbers: List[str]) -> None:
            for number in numbers:
                self.log(f"[Music] 创建编号 {number}, 曲目数={track_count}")
                automator.build_music_containers(number, track_count)
            automator.build_events(numbers)

        self.run_task("一键搭建", task)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
