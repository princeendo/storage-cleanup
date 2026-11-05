"""Known developer cache locations: each says why it is safe and how it comes back."""

from dataclasses import dataclass
from typing import Final

from storage_cleanup.scan.categories import DEV_CACHES
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.measure import measure
from storage_cleanup.scan.model import AgeSignal, Item, ItemKind, SizeSource, Skipped


@dataclass(frozen=True)
class KnownCache:
    rel_path: str
    name: str
    why_safe: str
    regenerates: str


_REDOWNLOAD = "Downloaded again the next time a build or install needs it."

KNOWN_CACHES: Final[tuple[KnownCache, ...]] = (
    KnownCache(
        "Library/Developer/Xcode/DerivedData",
        "Xcode DerivedData",
        "Build products and indexes only; no source code.",
        "Rebuilt by Xcode on the next build.",
    ),
    KnownCache(
        "Library/Developer/Xcode/iOS DeviceSupport",
        "Xcode device support files",
        "Debug symbols copied from connected devices.",
        "Copied again when a device is next connected for debugging.",
    ),
    KnownCache(
        "Library/Developer/CoreSimulator/Caches",
        "iOS Simulator caches",
        "Simulator caches, not simulator devices or their data.",
        "Rebuilt by the Simulator when needed.",
    ),
    KnownCache(
        "Library/Caches/pip", "pip cache", "Downloaded Python wheels.", _REDOWNLOAD
    ),
    KnownCache(".cache/pip", "pip cache", "Downloaded Python wheels.", _REDOWNLOAD),
    KnownCache(
        ".cache/uv", "uv cache", "Downloaded and built Python packages.", _REDOWNLOAD
    ),
    KnownCache(".npm/_cacache", "npm cache", "Downloaded npm packages.", _REDOWNLOAD),
    KnownCache(
        ".bun/install/cache", "Bun cache", "Downloaded npm packages.", _REDOWNLOAD
    ),
    KnownCache(
        "Library/pnpm/store", "pnpm store", "Downloaded npm packages.", _REDOWNLOAD
    ),
    KnownCache(
        "Library/Caches/Yarn", "Yarn cache", "Downloaded npm packages.", _REDOWNLOAD
    ),
    KnownCache(
        ".gradle/caches",
        "Gradle caches",
        "Downloaded Java dependencies and build caches.",
        _REDOWNLOAD,
    ),
    KnownCache(
        ".m2/repository",
        "Maven repository",
        "Downloaded Java dependencies.",
        _REDOWNLOAD,
    ),
    KnownCache(
        ".cargo/registry",
        "Cargo registry",
        "Downloaded Rust crates and their index.",
        _REDOWNLOAD,
    ),
    KnownCache(
        "Library/Caches/go-build",
        "Go build cache",
        "Compiled Go packages.",
        "Rebuilt by the Go toolchain on the next build.",
    ),
    KnownCache("go/pkg/mod", "Go module cache", "Downloaded Go modules.", _REDOWNLOAD),
    KnownCache(
        "Library/Caches/ms-playwright",
        "Playwright browsers",
        "Browser binaries Playwright downloaded for tests.",
        "Downloaded again by `playwright install`.",
    ),
    KnownCache(
        ".cache/huggingface",
        "Hugging Face cache",
        "Downloaded models and datasets.",
        "Downloaded again when a model is next loaded (large models take a while).",
    ),
)


def collect_dev_caches(ctx: ScanContext) -> CollectorResult:
    items: list[Item] = []
    skipped: list[Skipped] = []
    for cache in KNOWN_CACHES:
        path = ctx.home / cache.rel_path
        if not path.exists():
            continue
        m, sk = measure(path, collector="dev-caches")
        skipped.extend(sk)
        items.append(
            Item(
                id=item_id(ItemKind.CACHE, str(path)),
                kind=ItemKind.CACHE,
                name=cache.name,
                path=str(path),
                size_bytes=m.size_bytes,
                size_source=SizeSource.ALLOCATED,
                categories=(DEV_CACHES,),
                age_signal=AgeSignal.LAST_MODIFIED if m.newest_mtime else None,
                age_timestamp=m.newest_mtime,
                file_count=m.file_count,
                details={"why_safe": cache.why_safe, "regenerates": cache.regenerates},
            )
        )
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
