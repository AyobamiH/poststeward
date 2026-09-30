"""Direct single-post / reply-chain publication with durable receipts.

Scheduled/frozen campaigns use scheduler.py. This module gives explicit manual
publishing the same lossless X/Threads publication shape without creating a
second consequence model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ocpf_post.model import PublishReceipt
from ocpf_post.providers.base import AmbiguousProviderEffect, ProviderRejected, ProviderUnavailable
from ocpf_post.publication_payload import build_publication
from ocpf_post.state import append_receipt


@dataclass
class DirectPublicationStopped(RuntimeError):
    status: str
    message: str

    def __str__(self) -> str:
        return self.message


def _receipt(
    *,
    campaign: str,
    provider_name: str,
    account: Any,
    status: str,
    text_sha256: str,
    recorded_at: str,
    post_id: str | None = None,
    url: str | None = None,
    readback_verified: bool = False,
    detail: str | None = None,
    publication_type: str = "single",
    part_count: int = 1,
    publication_part_ids: list[str] | None = None,
    completed_part_count: int | None = None,
    failed_part_index: int | None = None,
) -> PublishReceipt:
    return PublishReceipt(
        campaign=campaign,
        provider=provider_name,
        account_id=account.account_id,
        username=account.username,
        status=status,
        text_sha256=text_sha256,
        recorded_at=recorded_at,
        post_id=post_id,
        url=url,
        readback_verified=readback_verified,
        detail=detail,
        publication_type=publication_type,
        part_count=part_count,
        publication_part_ids=list(publication_part_ids or []),
        completed_part_count=completed_part_count,
        failed_part_index=failed_part_index,
    )


def publish(
    *,
    provider: Any,
    account: Any,
    campaign: str,
    provider_name: str,
    text: str,
    now: Callable[[], str],
) -> dict[str, Any]:
    """Publish one logical manual publication, threading X/Threads when needed."""
    publication = build_publication(provider_name, text)
    parts = publication["parts"]
    if publication["publication_type"] == "single":
        try:
            created = provider.publish(parts[0]["text"])
        except AmbiguousProviderEffect as exc:
            receipt = _receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                status="ambiguous_effect",
                text_sha256=publication["text_sha256"],
                recorded_at=now(),
                detail=str(exc),
            )
            append_receipt(receipt.to_dict())
            raise DirectPublicationStopped(
                "ambiguous_effect",
                "Provider effect is ambiguous; a durable receipt blocks blind retry.",
            ) from exc
        post_id = str(created["id"])
        url = provider.post_url(account, post_id)
        provisional = _receipt(
            campaign=campaign,
            provider_name=provider_name,
            account=account,
            status="published_unverified",
            text_sha256=publication["text_sha256"],
            recorded_at=now(),
            post_id=post_id,
            url=url,
            detail="Provider creation response returned a post id; readback pending.",
        )
        append_receipt(provisional.to_dict())
        try:
            verified = bool(provider.verify_post(post_id, parts[0]["text"]))
        except (ProviderRejected, ProviderUnavailable, OSError, TimeoutError):
            verified = False
        if verified:
            final = _receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                status="published_verified",
                text_sha256=publication["text_sha256"],
                recorded_at=now(),
                post_id=post_id,
                url=provider.post_url(account, post_id),
                readback_verified=True,
                detail="Provider creation response and readback agree on id/text.",
            )
            append_receipt(final.to_dict())
            return {"status": "published_verified", "url": final.url, "receipt": final.to_dict(),
                    "publication": publication}
        return {"status": "published_unverified", "url": url, "receipt": provisional.to_dict(),
                "publication": publication}

    ids: list[str] = []
    verified_parts = 0
    root_id: str | None = None
    root_url: str | None = None
    for part in parts:
        index = int(part["index"])
        reply_to = ids[-1] if ids else None
        try:
            created = provider.publish(part["text"]) if reply_to is None else provider.reply(part["text"], reply_to)
        except AmbiguousProviderEffect as exc:
            receipt = _receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                status="ambiguous_effect",
                text_sha256=publication["text_sha256"],
                recorded_at=now(),
                post_id=root_id,
                url=root_url,
                detail=f"Thread part {index} may have produced an external effect; no blind continuation/retry.",
                publication_type="thread",
                part_count=len(parts),
                publication_part_ids=ids,
                completed_part_count=len(ids),
                failed_part_index=index,
            )
            append_receipt(receipt.to_dict())
            raise DirectPublicationStopped("ambiguous_effect", str(receipt.detail)) from exc
        except ProviderRejected as exc:
            if not ids:
                raise
            receipt = _receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                status="partial_effect",
                text_sha256=publication["text_sha256"],
                recorded_at=now(),
                post_id=root_id,
                url=root_url,
                detail=f"Thread stopped after {len(ids)} durable parts because part {index} was rejected.",
                publication_type="thread",
                part_count=len(parts),
                publication_part_ids=ids,
                completed_part_count=len(ids),
                failed_part_index=index,
            )
            append_receipt(receipt.to_dict())
            raise DirectPublicationStopped("partial_effect", str(receipt.detail)) from exc

        post_id = str(created["id"])
        ids.append(post_id)
        if root_id is None:
            root_id = post_id
            root_url = provider.post_url(account, post_id)
        try:
            verified = bool(provider.verify_post(post_id, part["text"]))
        except (ProviderRejected, ProviderUnavailable, OSError, TimeoutError):
            verified = False
        if verified:
            verified_parts += 1

        if index < len(parts):
            # If the process dies before the next part, this terminal partial
            # receipt prevents a later invocation from replaying the root/thread.
            checkpoint = _receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                status="partial_effect",
                text_sha256=publication["text_sha256"],
                recorded_at=now(),
                post_id=root_id,
                url=root_url,
                detail=f"Thread in progress after {len(ids)}/{len(parts)} durable parts.",
                publication_type="thread",
                part_count=len(parts),
                publication_part_ids=ids,
                completed_part_count=len(ids),
            )
            append_receipt(checkpoint.to_dict())

    provisional = _receipt(
        campaign=campaign,
        provider_name=provider_name,
        account=account,
        status="published_unverified",
        text_sha256=publication["text_sha256"],
        recorded_at=now(),
        post_id=root_id,
        url=root_url,
        detail=f"All {len(parts)} thread parts returned durable provider IDs.",
        publication_type="thread",
        part_count=len(parts),
        publication_part_ids=ids,
        completed_part_count=len(ids),
    )
    append_receipt(provisional.to_dict())
    if verified_parts == len(parts):
        final = _receipt(
            campaign=campaign,
            provider_name=provider_name,
            account=account,
            status="published_verified",
            text_sha256=publication["text_sha256"],
            recorded_at=now(),
            post_id=root_id,
            url=root_url,
            readback_verified=True,
            detail=f"All {len(parts)} thread parts were independently read back.",
            publication_type="thread",
            part_count=len(parts),
            publication_part_ids=ids,
            completed_part_count=len(ids),
        )
        append_receipt(final.to_dict())
        return {"status": "published_verified", "url": root_url, "receipt": final.to_dict(),
                "publication": publication}
    return {"status": "published_unverified", "url": root_url, "receipt": provisional.to_dict(),
            "publication": publication}
