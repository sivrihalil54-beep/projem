"""
Captcha: aynı (veya çok benzer) basamak görselini frekans / benzerlik ile bulma.

Strateji (OCR yok):
  1) Her karo için PNG baytlarından ortalama hash (aHash, 8x8 gri).
  2) Hamming mesafesi ile kümeler; silik / hafif farklı rasterlar aynı kümede.
  3) En kalabalık kümeden bir indeks seçilir (tıklama hedefi).

Yalnızca yetkili test / kendi sisteminiz için kullanın; üçüncü taraf ToS ihlali olabilir.
"""

from __future__ import annotations

import base64
import io
from collections import defaultdict
from dataclasses import dataclass

from PIL import Image


def _open_png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def image_to_average_hash_bits(
    data: bytes,
    *,
    size: int = 8,
    shrink: int = 32,
) -> int:
    """
    Gri resmi `shrink` boyuna indir, `size`x`size` aHash.
    Ortalama piksel eşiği; hafif solukluk farklarına görece dayanıklı.
    """
    img = _open_png(data).convert("L")
    if img.size[0] != shrink or img.size[1] != shrink:
        img = img.resize((shrink, shrink), Image.Resampling.LANCZOS)
    small = img.resize((size, size), Image.Resampling.LANCZOS)
    if hasattr(small, "get_flattened_data"):
        pixels = list(small.get_flattened_data())
    else:
        pixels = list(small.getdata())
    avg = sum(pixels) / max(len(pixels), 1)
    bits = 0
    for i, p in enumerate(pixels):
        if p >= avg:
            bits |= 1 << i
    return bits


def hamming64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def png_to_base64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def cluster_by_similarity(hashes: list[int], max_hamming: int) -> list[int]:
    """Her indeks için union-find kökü (aynı küme = aynı sanılan görsel)."""
    n = len(hashes)
    if n == 0:
        return []
    parent = list(range(n))

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if hamming64(hashes[i], hashes[j]) <= max_hamming:
                union(i, j)
    return [find(i) for i in range(n)]


@dataclass(frozen=True)
class DominantGroupResult:
    """Frekans analizi: en buyuk kume ve guven SKORU (BLS anti-karisik secim)."""

    indices: tuple[int, ...]
    cluster_sizes_desc: tuple[int, ...]
    dominant_count: int
    tile_count: int
    dominant_fraction: float
    is_confident: bool
    reason: str


def detect_dominant_group(
    png_tiles: list[bytes],
    *,
    max_hamming: int = 10,
    min_dominant_fraction: float = 0.40,
    hash_size: int = 8,
    shrink: int = 32,
) -> DominantGroupResult:
    """
    En cok tekrar eden gorsel kumesini bulur; kume toplamin yuzdesi dusukse veya
    esitlikte 'guven yok' (yenile / tiklama yapma).
    """
    n = len(png_tiles)
    if n == 0:
        return DominantGroupResult(
            (), (), 0, 0, 0.0, False, "empty_tiles",
        )
    hashes = [
        image_to_average_hash_bits(b, size=hash_size, shrink=shrink) for b in png_tiles
    ]
    roots = cluster_by_similarity(hashes, max_hamming=max_hamming)
    buckets: dict[int, list[int]] = defaultdict(list)
    for idx, r in enumerate(roots):
        buckets[r].append(idx)
    sizes_sorted = sorted((len(v) for v in buckets.values()), reverse=True)
    winning = max(buckets.values(), key=len)
    dom_n = len(winning)
    frac = dom_n / n
    max_sz = sizes_sorted[0]
    n_at_max = sum(1 for s in sizes_sorted if s == max_sz)
    if n_at_max > 1:
        return DominantGroupResult(
            tuple(sorted(winning)),
            tuple(sizes_sorted),
            dom_n,
            n,
            frac,
            False,
            "tie_top_clusters",
        )
    if frac < min_dominant_fraction:
        return DominantGroupResult(
            tuple(sorted(winning)),
            tuple(sizes_sorted),
            dom_n,
            n,
            frac,
            False,
            "below_min_fraction",
        )
    return DominantGroupResult(
        tuple(sorted(winning)),
        tuple(sizes_sorted),
        dom_n,
        n,
        frac,
        True,
        "ok",
    )


def majority_visual_first_index(
    png_tiles: list[bytes],
    *,
    max_hamming: int = 10,
    hash_size: int = 8,
    shrink: int = 32,
) -> int:
    """
    En çok tekrar eden görsel grubundan **ilk** karo indeksini döndürür.

    Args:
        png_tiles: Her karonun PNG ekran görüntüsü veya img src baytları.
        max_hamming: İki hash bu mesafeden yakınsa "aynı basamak" sayılır (9–14 tipik).
    """
    if not png_tiles:
        raise ValueError("png_tiles boş")
    hashes = [
        image_to_average_hash_bits(b, size=hash_size, shrink=shrink) for b in png_tiles
    ]
    roots = cluster_by_similarity(hashes, max_hamming=max_hamming)
    buckets: dict[int, list[int]] = defaultdict(list)
    for idx, r in enumerate(roots):
        buckets[r].append(idx)
    winning = max(buckets.values(), key=len)
    return winning[0]


def majority_visual_all_indices(
    png_tiles: list[bytes],
    *,
    max_hamming: int = 10,
    hash_size: int = 8,
    shrink: int = 32,
) -> list[int]:
    """En kalabalık kümedeki tüm karo indeksleri (sıralı)."""
    if not png_tiles:
        raise ValueError("png_tiles boş")
    hashes = [
        image_to_average_hash_bits(b, size=hash_size, shrink=shrink) for b in png_tiles
    ]
    roots = cluster_by_similarity(hashes, max_hamming=max_hamming)
    buckets: dict[int, list[int]] = defaultdict(list)
    for idx, r in enumerate(roots):
        buckets[r].append(idx)
    winning = max(buckets.values(), key=len)
    return sorted(winning)


def fingerprint_stats(
    png_tiles: list[bytes],
    *,
    max_hamming: int = 10,
    hash_size: int = 8,
    shrink: int = 32,
) -> dict[str, object]:
    """Debug: küme boyutları, hash'ler, base64 önizlemeler."""
    hashes = [
        image_to_average_hash_bits(b, size=hash_size, shrink=shrink) for b in png_tiles
    ]
    roots = cluster_by_similarity(hashes, max_hamming=max_hamming)
    buckets: dict[int, list[int]] = defaultdict(list)
    for idx, r in enumerate(roots):
        buckets[r].append(idx)
    b64 = [png_to_base64(b)[:80] + "..." for b in png_tiles]
    return {
        "cluster_count": len(buckets),
        "cluster_sizes": sorted((len(v) for v in buckets.values()), reverse=True),
        "hashes_hex": [f"{h:016x}" for h in hashes],
        "base64_preview": b64,
        "picked_index": majority_visual_first_index(
            png_tiles,
            max_hamming=max_hamming,
            hash_size=hash_size,
            shrink=shrink,
        ),
    }
