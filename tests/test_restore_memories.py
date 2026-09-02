"""恢复脚本的真跑测试。

存在的理由：这个脚本只跑一次，跑错就是记忆丢了或被覆盖——没有第二次机会。
所以三件事必须钉死：原 ID 保留、本机已有的绝不覆盖、默认不写盘。
"""
import json
import os
from pathlib import Path

import restore_memories as rm


def _backup(tmp_path: Path, buckets: list[dict]) -> str:
    d = tmp_path / "bk"
    d.mkdir()
    (d / "all_buckets.json").write_text(json.dumps(buckets, ensure_ascii=False),
                                        encoding="utf-8")
    return str(d)


def _b(bid, content="正文", **meta):
    meta = {"id": bid, "type": "dynamic", "domain": ["日常"], "name": "小事", **meta}
    return {"id": bid, "metadata": meta, "content": content}


def test_dry_run_writes_nothing(tmp_path):
    base = tmp_path / "buckets"
    base.mkdir()
    out = rm.restore(_backup(tmp_path, [_b("aaa111")]), str(base), write=False)
    assert out["added"] == ["aaa111"]
    assert list(base.rglob("*.md")) == []          # 预演一个字都不许写


def test_write_keeps_original_id_and_lands_in_the_right_folder(tmp_path):
    base = tmp_path / "buckets"
    base.mkdir()
    rm.restore(_backup(tmp_path, [
        _b("aaa111"),
        _b("bbb222", type="permanent", domain=["核心"], name="准则"),
        _b("ccc333", type="feel", domain=[], name=""),
    ]), str(base), write=True)

    assert (base / "dynamic" / "日常" / "小事_aaa111.md").exists()
    assert (base / "permanent" / "核心" / "准则_bbb222.md").exists()
    assert (base / "feel" / "沉淀物" / "ccc333.md").exists()
    # 原 ID 必须原样保留，否则 feel 的 source_bucket 全断了
    assert 'id: "aaa111"' in (base / "dynamic" / "日常" / "小事_aaa111.md").read_text(encoding="utf-8")


def test_existing_bucket_is_never_overwritten(tmp_path):
    """本机那份可能已经被 dream/trace 改过，比备份新——只能跳过。"""
    base = tmp_path / "buckets"
    live = base / "dynamic" / "日常"
    live.mkdir(parents=True)
    f = live / "小事_aaa111.md"
    f.write_text('---\nid: "aaa111"\n---\n\n本机这份是新的\n', encoding="utf-8")

    out = rm.restore(_backup(tmp_path, [_b("aaa111", content="备份里的旧正文")]),
                     str(base), write=True)
    assert out["skipped"] == ["aaa111"] and out["added"] == []
    assert "本机这份是新的" in f.read_text(encoding="utf-8")


def test_domain_name_cannot_escape_the_buckets_dir(tmp_path):
    """域名/桶名来自备份文件，不能让它写到 buckets 外面去。"""
    base = tmp_path / "buckets"
    base.mkdir()
    rm.restore(_backup(tmp_path, [_b("ddd444", domain=["../../etc"], name="../x")]),
               str(base), write=True)
    written = list(base.rglob("*.md"))
    assert len(written) == 1
    assert str(base.resolve()) in str(written[0].resolve())


# --------------------------------------------------------------------------
# /api/export 打包出来的 tar.gz 解开后长这样：ombre_data/{permanent,dynamic,feel}/…
# 这条路比逐条拉 611 次稳，所以也要真跑一遍。
# --------------------------------------------------------------------------

def _export_tree(tmp_path: Path) -> Path:
    root = tmp_path / "ombre_data"
    (root / "dynamic" / "日常").mkdir(parents=True)
    (root / "permanent" / "核心").mkdir(parents=True)
    (root / "feel" / "沉淀物").mkdir(parents=True)
    (root / "dynamic" / "日常" / "小事_aaa111.md").write_text(
        '---\nid: "aaa111"\ntype: "dynamic"\n---\n\nRender 上的旧事\n', encoding="utf-8")
    (root / "permanent" / "核心" / "准则_bbb222.md").write_text(
        '---\nid: "bbb222"\n---\n\n准则\n', encoding="utf-8")
    (root / "feel" / "沉淀物" / "ccc333.md").write_text(
        '---\nid: "ccc333"\n---\n\n沉淀\n', encoding="utf-8")
    (root / "embeddings.db").write_bytes(b"\x00sqlite-not-md")   # 绝不能被拷过去
    return root


def test_export_tarball_tree_is_copied_structure_intact(tmp_path):
    base = tmp_path / "buckets"
    base.mkdir()
    _export_tree(tmp_path)
    out = rm.restore(str(tmp_path), str(base), write=True)

    assert sorted(out["added"]) == ["aaa111", "bbb222", "ccc333"]
    assert (base / "dynamic" / "日常" / "小事_aaa111.md").exists()
    assert (base / "permanent" / "核心" / "准则_bbb222.md").exists()
    assert (base / "feel" / "沉淀物" / "ccc333.md").exists()
    # 向量库是本机自己的，两边不能互相覆盖
    assert not (base / "embeddings.db").exists()


def test_export_tree_never_overwrites_local_version(tmp_path):
    base = tmp_path / "buckets"
    live = base / "dynamic" / "日常"
    live.mkdir(parents=True)
    f = live / "小事_aaa111.md"
    f.write_text('---\nid: "aaa111"\n---\n\n本机这份是新的\n', encoding="utf-8")
    _export_tree(tmp_path)

    out = rm.restore(str(tmp_path), str(base), write=True)
    assert "aaa111" in out["skipped"] and "aaa111" not in out["added"]
    assert "本机这份是新的" in f.read_text(encoding="utf-8")


def test_export_tree_dry_run_writes_nothing(tmp_path):
    base = tmp_path / "buckets"
    base.mkdir()
    _export_tree(tmp_path)
    out = rm.restore(str(tmp_path), str(base), write=False)
    assert len(out["added"]) == 3
    assert list(base.rglob("*.md")) == []
