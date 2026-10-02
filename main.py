import asyncio
import contextlib
import errno
import json
import math
import os
import pwd
import socket
import stat
import uuid

import decky


SOCKET_PATH = "/tmp/decky-dialogs.sock"
MAX_MESSAGE_SIZE = 64 * 1024
DEFAULT_PROMPT_TIMEOUT = 30.0
MIN_PROMPT_TIMEOUT = 1.0
MAX_PROMPT_TIMEOUT = 3600.0


class Plugin:
    def __init__(self):
        self._server = None
        self._writers = set()
        self._client_tasks = set()
        self._pending_prompts = {}
        self._socket_inode = None
        self._stopping = False

    def _prepare_socket_path(self):
        try:
            info = os.lstat(SOCKET_PATH)
        except FileNotFoundError:
            return

        if not stat.S_ISSOCK(info.st_mode):
            raise RuntimeError(
                f"Refusing to remove non-socket path: {SOCKET_PATH}"
            )

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.25)

        try:
            probe.connect(SOCKET_PATH)
        except OSError as error:
            if error.errno in (errno.ECONNREFUSED, errno.ENOENT):
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(SOCKET_PATH)
                return
            raise
        else:
            raise RuntimeError(
                f"Another Decky Dialogs server is already using {SOCKET_PATH}"
            )
        finally:
            probe.close()

    def _set_socket_permissions(self):
        try:
            deck_user = pwd.getpwnam("deck")
            os.chown(SOCKET_PATH, deck_user.pw_uid, deck_user.pw_gid)
            os.chmod(SOCKET_PATH, 0o660)
        except (KeyError, PermissionError, OSError):
            os.chmod(SOCKET_PATH, 0o666)

    @staticmethod
    def _text(value, default="", maximum=4096):
        if value is None:
            value = default
        return str(value)[:maximum]

    @staticmethod
    def _timeout(value):
        if value is None:
            return DEFAULT_PROMPT_TIMEOUT
        if isinstance(value, bool):
            raise ValueError("timeout must be a number of seconds")
        try:
            timeout = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("timeout must be a number of seconds") from error
        if not math.isfinite(timeout):
            raise ValueError("timeout must be finite")
        return max(MIN_PROMPT_TIMEOUT, min(timeout, MAX_PROMPT_TIMEOUT))

    def _buttons(self, value):
        if value is None:
            value = [{"id": "ok", "label": "OK"}]
        if not isinstance(value, list) or not value:
            raise ValueError("buttons must be a non-empty array")
        if len(value) > 8:
            raise ValueError("at most 8 buttons are supported")

        buttons = []
        seen = set()
        for index, button in enumerate(value):
            if isinstance(button, str):
                button_id = self._text(button, maximum=64)
                label = self._text(button, maximum=128)
            elif isinstance(button, dict):
                button_id = self._text(
                    button.get("id"),
                    default=f"button-{index + 1}",
                    maximum=64,
                )
                label = self._text(
                    button.get("label"),
                    default=button_id,
                    maximum=128,
                )
            else:
                raise ValueError("each button must be a string or object")

            if not button_id or button_id in seen:
                raise ValueError("button IDs must be non-empty and unique")
            seen.add(button_id)
            buttons.append({"id": button_id, "label": label or button_id})

        return buttons

    def _input(self, value):
        if value in (None, False):
            return None
        if value is True:
            value = {}
        if not isinstance(value, dict):
            raise ValueError("input must be a boolean or object")
        return {
            "label": self._text(value.get("label"), "Input", 128),
            "placeholder": self._text(value.get("placeholder"), "", 256),
            "default": self._text(
                value.get("default", value.get("value", "")), "", 4096
            ),
        }

    async def _write_json(self, writer, value):
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        writer.write(data.encode("utf-8") + b"\n")
        await writer.drain()

    async def _handle_notification(self, payload, writer):
        if isinstance(payload, str):
            title = "Decky Dialogs"
            body = payload
            timeout = None
        else:
            title = self._text(payload.get("title"), "Decky Dialogs", 256)
            body = payload.get("body")
            if body is None:
                body = payload.get("message", "")
            body = self._text(body, maximum=4096)
            timeout = payload.get("timeout")
            if timeout is not None:
                timeout = self._timeout(timeout)

        decky.logger.info(f"Decky Dialogs is sending a toast: {title}")
        await decky.emit("decky_dialogs_notify", title, body, timeout)
        writer.write(b"OK\n")
        await writer.drain()

    async def _handle_prompt(self, payload, writer):
        request_id = self._text(payload.get("id"), maximum=128)
        if not request_id:
            request_id = str(uuid.uuid4())
        if request_id in self._pending_prompts:
            raise ValueError(f"prompt ID is already active: {request_id}")

        title = self._text(payload.get("title"), "Decky Dialogs", 256)
        body = payload.get("body")
        if body is None:
            body = payload.get("text", payload.get("message", ""))
        body = self._text(body, maximum=16384)
        timeout = self._timeout(payload.get("timeout"))
        buttons = self._buttons(payload.get("buttons"))
        input_options = self._input(payload.get("input"))

        request = {
            "id": request_id,
            "title": title,
            "body": body,
            "buttons": buttons,
            "timeout": timeout,
            "input": input_options,
        }

        loop = asyncio.get_running_loop()
        response_future = loop.create_future()
        self._pending_prompts[request_id] = response_future

        decky.logger.info(f"Decky Dialogs is showing a prompt: {title} ({request_id})")

        try:
            await decky.emit("decky_dialogs_prompt", request)
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(response_future), timeout=timeout
                )
            except asyncio.TimeoutError:
                response = {"id": request_id, "status": "timeout"}
                await decky.emit("decky_dialogs_prompt_cancel", request_id)

            await self._write_json(writer, response)
        finally:
            current = self._pending_prompts.pop(request_id, None)
            if current is not None and not current.done():
                current.cancel()

    async def dialog_response(self, response):
        """Called by the Decky frontend when a dialog finishes."""
        if not isinstance(response, dict):
            return False

        request_id = self._text(response.get("id"), maximum=128)
        future = self._pending_prompts.get(request_id)
        if future is None or future.done():
            return False

        status = response.get("status")
        if status not in ("selected", "dismissed", "timeout", "error"):
            status = "error"

        result = {"id": request_id, "status": status}
        if status == "selected":
            result["button"] = self._text(response.get("button"), maximum=64)
        if "input" in response:
            result["input"] = self._text(response.get("input"), maximum=4096)
        if status == "error" and "message" in response:
            result["message"] = self._text(response.get("message"), maximum=1024)

        future.set_result(result)
        return True

    async def _handle_client(self, reader, writer):
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        self._writers.add(writer)

        try:
            raw_message = await asyncio.wait_for(reader.readline(), timeout=3.0)
            if not raw_message:
                return
            if len(raw_message) > MAX_MESSAGE_SIZE:
                raise ValueError("message exceeds maximum size")

            payload = json.loads(raw_message.decode("utf-8"))
            if isinstance(payload, str):
                await self._handle_notification(payload, writer)
            elif isinstance(payload, dict):
                message_type = payload.get("type", "notify")
                if message_type == "notify":
                    await self._handle_notification(payload, writer)
                elif message_type == "prompt":
                    await self._handle_prompt(payload, writer)
                else:
                    raise ValueError(f"unsupported message type: {message_type}")
            else:
                raise ValueError("message must be a JSON object or string")

        except asyncio.TimeoutError:
            decky.logger.warning("Decky Dialogs client timed out while sending")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            decky.logger.exception("Decky Dialogs failed to process a socket message")
            with contextlib.suppress(Exception):
                await self._write_json(
                    writer,
                    {"status": "error", "message": str(error)[:1024]},
                )
        finally:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
            if task is not None:
                self._client_tasks.discard(task)

    async def _main(self):
        self._stopping = False
        self._prepare_socket_path()
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=SOCKET_PATH,
            limit=MAX_MESSAGE_SIZE + 1,
        )
        self._socket_inode = os.lstat(SOCKET_PATH).st_ino
        self._set_socket_permissions()
        decky.logger.info(f"Decky Dialogs is listening on {SOCKET_PATH}")

        try:
            await self._server.serve_forever()
        except asyncio.CancelledError:
            if not self._stopping:
                raise

    async def _unload(self):
        self._stopping = True
        server = self._server
        self._server = None

        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()

        for request_id, future in list(self._pending_prompts.items()):
            if not future.done():
                future.set_result(
                    {
                        "id": request_id,
                        "status": "error",
                        "message": "Plugin unloaded",
                    }
                )

        await asyncio.sleep(0)

        for writer in list(self._writers):
            writer.close()

        current_task = asyncio.current_task()
        client_tasks = [
            task
            for task in self._client_tasks
            if task is not current_task and not task.done()
        ]
        for task in client_tasks:
            task.cancel()
        if client_tasks:
            await asyncio.gather(*client_tasks, return_exceptions=True)

        self._pending_prompts.clear()
        self._writers.clear()
        self._client_tasks.clear()

        try:
            info = os.lstat(SOCKET_PATH)
            if stat.S_ISSOCK(info.st_mode) and info.st_ino == self._socket_inode:
                os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass
        except Exception:
            decky.logger.exception("Decky Dialogs failed to remove its socket")

        self._socket_inode = None
        decky.logger.info("Decky Dialogs unloaded")
