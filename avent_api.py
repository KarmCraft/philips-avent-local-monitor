"""Minimal Philips Avent / Tuya Mobile SDK client.

Adapted from the MIT-licensed ``thekoma/aventproxy`` 2026.8.0 integration.
Only the password+MFA, account discovery, and signing functions required by
the localhost viewer are retained. User credentials are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA


# Public application constants extracted by the upstream project from the
# Philips Baby Monitor+ APK. They identify the app; they are not user account
# credentials. User SID/ecode/partner values are stored only in the EFS file.
TUYA_SIGNING_KEY = (
    "com.philips.ph.babymonitorplus"
    "_D2:D6:95:A1:1D:1B:84:F9:25:A9:45:6E:27:F4:45:E9:FD:87:C3:74"
    ":63:AA:8A:34:32:A6:6A:23:3B:0F:D5:0F"
    "_8n459nxk9g98gqgcwrpk3csv97uuwajm"
    "_a3nfht4ufwfw9cmkspaftv4x89cx58qx"
)
TUYA_APP_KEY = "wx3at9qprkhskvkcsyhm"
TUYA_PACKAGE_NAME = "com.philips.ph.babymonitorplus"
TUYA_CH_KEY = "071d81fa"

DATA_CENTER_HOSTS = {
    "eu": "a1.tuyaeu.com",
    "us": "a1.tuyaus.com",
    "in": "a1.tuyain.com",
    "cn": "a1.tuyacn.com",
}
PROBE_ORDER = ("eu", "us", "in", "cn")

SIGN_PARAM_WHITELIST = frozenset(
    [
        "a",
        "v",
        "lat",
        "lon",
        "lang",
        "deviceId",
        "appVersion",
        "ttid",
        "isH5",
        "h5Token",
        "os",
        "clientId",
        "postData",
        "time",
        "requestId",
        "et",
        "n4h5",
        "sid",
        "chKey",
        "sp",
    ]
)


class TuyaAPIError(Exception):
    """Tuya error with a safe machine-readable code."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class PendingLogin:
    email: str
    password: str
    country_code: str
    api: "AventAPI"
    data_center: str
    created_at: float

    def clear_password(self) -> None:
        self.password = ""


def new_device_id() -> str:
    return uuid.uuid4().hex


def _swap(value: str) -> str:
    if len(value) != 32:
        return value
    return value[8:16] + value[0:8] + value[24:32] + value[16:24]


def is_wrong_data_center(code: str) -> bool:
    upper = code.upper()
    if "PASSWD" in upper or "MFA" in upper or "CODE" in upper:
        return False
    return "NOT_EXIST" in upper or "SESSION" in upper or "REGION" in upper


def normalize_api_host(value: str | None) -> str | None:
    if not value:
        return None
    host = value.strip().removeprefix("https://").removeprefix("http://")
    host = host.split("/", 1)[0].strip()
    return host or None


class AventAPI:
    """Async client matching the Philips Baby Monitor+ Mobile SDK calls."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_host: str,
        country_code: str,
        sid: str = "",
        device_id: str | None = None,
    ) -> None:
        self.session = session
        self.api_host = api_host
        self.api_url = f"https://{api_host}/api.json"
        self.country_code = country_code
        self.sid = sid
        self.device_id = device_id or new_device_id()

    @staticmethod
    def encrypt_password(password: str, public_key: str) -> str:
        md5_password = hashlib.md5(password.encode()).hexdigest()
        pem = f"-----BEGIN PUBLIC KEY-----\n{public_key}\n-----END PUBLIC KEY-----"
        key = RSA.import_key(pem)
        return PKCS1_v1_5.new(key).encrypt(md5_password.encode()).hex()

    def _sign(self, params: dict[str, str]) -> str:
        filtered = {
            key: value
            for key, value in params.items()
            if key in SIGN_PARAM_WHITELIST and value
        }
        if filtered.get("postData"):
            filtered["postData"] = _swap(
                hashlib.md5(filtered["postData"].encode()).hexdigest()
            )
        value = "||".join(f"{key}={filtered[key]}" for key in sorted(filtered))
        return hmac.new(
            TUYA_SIGNING_KEY.encode(), value.encode(), hashlib.sha256
        ).hexdigest()

    def _params(
        self, action: str, version: str = "1.0", post_data: Any = None
    ) -> dict[str, str]:
        params = {
            "a": action,
            "v": version,
            "time": str(int(time.time())),
            "appVersion": "1.8.0",
            "appRnVersion": "5.92",
            "channel": "oem",
            "chKey": TUYA_CH_KEY,
            "clientId": TUYA_APP_KEY,
            "cp": "gzip",
            "deviceCoreVersion": "6.7.0",
            "deviceId": self.device_id,
            "et": "0.0.1",
            "nd": "1",
            "lang": "en_US",
            "os": "Android",
            "osSystem": "14",
            "platform": "avent_local_viewer",
            "requestId": str(uuid.uuid4()),
            "sdkVersion": "6.7.0",
            "sid": self.sid,
            "timeZoneId": "Europe/Zurich",
            "ttid": f"sdk_international@{TUYA_APP_KEY}",
        }
        if post_data is not None:
            params["postData"] = (
                post_data if isinstance(post_data, str) else json.dumps(post_data)
            )
        params["sign"] = self._sign(params)
        return params

    async def call(
        self,
        action: str,
        version: str = "1.0",
        post_data: Any = None,
        extra_params: dict[str, str] | None = None,
    ) -> Any:
        params = self._params(action, version, post_data)
        if extra_params:
            params.update(extra_params)
        async with self.session.post(
            self.api_url,
            data=params,
            headers={
                "User-Agent": "Thing-UA=APP/Android/1.8.0/SDK/6.7.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        ) as response:
            result = await response.json(content_type=None)
        if not result.get("success"):
            raise TuyaAPIError(
                result.get("errorCode", "UNKNOWN"),
                result.get("errorMsg", "Unknown error"),
            )
        return result.get("result")

    async def rsa_token(self, email: str) -> dict[str, Any]:
        return await self.call(
            "thing.m.user.username.token.get",
            "2.0",
            {
                "countryCode": self.country_code,
                "username": email,
                "isUid": False,
            },
        )

    async def login_password(
        self, email: str, encrypted_password: str, token: str, mfa_code: str = ""
    ) -> dict[str, Any]:
        old_sid = self.sid
        self.sid = ""
        try:
            return await self.call(
                "thing.m.user.email.password.login",
                "3.0",
                {
                    "countryCode": self.country_code,
                    "email": email,
                    "passwd": encrypted_password,
                    "token": token,
                    "ifencrypt": 1,
                    "options": json.dumps({"group": 1, "mfaCode": mfa_code}),
                },
            )
        except TuyaAPIError:
            self.sid = old_sid
            raise

    async def trigger_mfa(
        self, email: str, encrypted_password: str, token: str
    ) -> Any:
        old_sid = self.sid
        self.sid = ""
        try:
            return await self.call(
                "thing.m.user.username.mfa.code.get",
                "1.0",
                {
                    "countryCode": self.country_code,
                    "username": email,
                    "passwd": encrypted_password,
                    "token": token,
                    "ifencrypt": 1,
                    "options": json.dumps({"group": 1, "mfaCode": "null"}),
                },
            )
        finally:
            self.sid = old_sid

    async def user_info(self) -> dict[str, Any]:
        return await self.call("smartlife.m.user.info.get")

    async def homes(self) -> list[dict[str, Any]]:
        result = await self.call("m.life.home.space.list")
        return result if isinstance(result, list) else []

    async def discover_cameras(self) -> list[dict[str, str]]:
        devices: list[dict[str, Any]] = []
        seen: set[str] = set()
        homes = await self.homes()

        def collect(candidates: Any) -> None:
            if not isinstance(candidates, list):
                return
            for device in candidates:
                if not isinstance(device, dict):
                    continue
                device_id = device.get("devId") or device.get("deviceId") or device.get("id")
                category = device.get("category", "")
                if device_id and device_id not in seen and category in {"sp", "dghsxj"}:
                    seen.add(device_id)
                    devices.append(device)

        for home in homes:
            gid = str(home.get("gid", ""))
            if not gid:
                continue
            for version in ("2.0", "1.0"):
                try:
                    rooms = await self.call(
                        "tuya.m.location.get",
                        version,
                        {"gid": gid},
                        {"gid": gid},
                    )
                    if isinstance(rooms, list):
                        for room in rooms:
                            collect(room.get("deviceList", []))
                    if devices:
                        break
                except TuyaAPIError:
                    continue
            if not devices:
                try:
                    collect(
                        await self.call(
                            "tuya.m.my.group.device.list", extra_params={"gid": gid}
                        )
                    )
                except TuyaAPIError:
                    pass

        if devices:
            return [self._camera_record(device) for device in devices]

        for home in homes:
            gid = str(home.get("gid", ""))
            if not gid:
                continue
            try:
                collect(
                    await self.call(
                        "tuya.m.my.group.device.relation.list",
                        extra_params={"gid": gid},
                    )
                )
            except TuyaAPIError:
                pass

        if not devices:
            try:
                collect(await self.call("tuya.m.device.list.get"))
            except TuyaAPIError:
                pass

        return [self._camera_record(device) for device in devices]

    @staticmethod
    def _camera_record(device: dict[str, Any]) -> dict[str, str]:
        return {
            "camera_id": str(
                device.get("devId") or device.get("deviceId") or device.get("id") or ""
            ),
            "camera_name": str(
                device.get("name") or device.get("deviceName") or "Baby Monitor"
            ),
            "product_id": str(
                device.get("productId") or device.get("productKey") or ""
            ),
        }


async def begin_login(
    session: aiohttp.ClientSession,
    *,
    email: str,
    password: str,
    country_code: str,
) -> PendingLogin:
    """Locate the account region and trigger its emailed MFA code."""
    first_error: TuyaAPIError | None = None
    for data_center in PROBE_ORDER:
        api = AventAPI(
            session,
            api_host=DATA_CENTER_HOSTS[data_center],
            country_code=country_code,
        )
        try:
            token = await api.rsa_token(email)
            encrypted = api.encrypt_password(password, token["pbKey"])
            try:
                await api.login_password(email, encrypted, token["token"])
            except TuyaAPIError as error:
                if error.code != "MFA_NEED_SEND_CODE":
                    raise

            token = await api.rsa_token(email)
            encrypted = api.encrypt_password(password, token["pbKey"])
            await api.trigger_mfa(email, encrypted, token["token"])
            return PendingLogin(
                email=email,
                password=password,
                country_code=country_code,
                api=api,
                data_center=data_center,
                created_at=time.time(),
            )
        except TuyaAPIError as error:
            first_error = first_error or error
            if is_wrong_data_center(error.code):
                continue
            raise
    raise first_error or TuyaAPIError("UNKNOWN", "No Tuya data center accepted the login")


async def complete_login(pending: PendingLogin, mfa_code: str) -> dict[str, Any]:
    token = await pending.api.rsa_token(pending.email)
    encrypted = pending.api.encrypt_password(pending.password, token["pbKey"])
    result = await pending.api.login_password(
        pending.email, encrypted, token["token"], mfa_code
    )
    pending.api.sid = result["sid"]
    domain = result.get("domain") if isinstance(result.get("domain"), dict) else {}
    reported_host = normalize_api_host(domain.get("mobileApiUrl"))
    if reported_host:
        pending.api.api_host = reported_host
        pending.api.api_url = f"https://{reported_host}/api.json"
    cameras = await pending.api.discover_cameras()
    if not cameras:
        raise TuyaAPIError("NO_CAMERAS", "No camera was discovered in this account")
    return {
        "signing_key": TUYA_SIGNING_KEY,
        "sid": result["sid"],
        "ecode": result.get("ecode", ""),
        "partner": result.get("partnerIdentity", ""),
        "app_key": TUYA_APP_KEY,
        "device_id": pending.api.device_id,
        "package_name": TUYA_PACKAGE_NAME,
        "api_host": pending.api.api_host,
        "talkback": False,
        "bridge_port": 38554,
        "cameras": cameras,
        "_site": {
            "schema": 1,
            "account_email": pending.email,
            "country_code": pending.country_code,
            "created_at": int(time.time()),
            "upstream": "thekoma/aventproxy@2026.8.0",
        },
    }
