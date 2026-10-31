import json

from app.models import Job


def make_job(**kwargs):
    defaults = dict(
        name="j",
        source="/mnt/a",
        destination="remote:b",
        sync_type="copy",
        transfers=4,
        checkers=8,
        retries=3,
        fast_list=False,
        bwlimit="",
        order_by_field="size",
        order_by_direction="mixed",
        order_by_mixed_window=50,
        compare_method="default",
        exclude_patterns="[]",
        extra_flags="{}",
    )
    defaults.update(kwargs)
    return Job(**defaults)


def test_order_by_mixed_includes_window():
    assert make_job().to_rclone_config()["OrderBy"] == "size,mixed,50"


def test_order_by_mixed_window_is_clamped():
    cfg = make_job(order_by_mixed_window=500).to_rclone_config()
    assert cfg["OrderBy"] == "size,mixed,100"


def test_order_by_asc_desc():
    desc = make_job(order_by_direction="desc").to_rclone_config()
    assert desc["OrderBy"] == "size,desc"
    asc = make_job(order_by_field="name", order_by_direction="asc").to_rclone_config()
    assert asc["OrderBy"] == "name,asc"


def test_compare_methods_map_to_rclone_keys():
    assert "SizeOnly" not in make_job().to_rclone_config()
    assert make_job(compare_method="size_only").to_rclone_config()["SizeOnly"] is True
    assert make_job(compare_method="checksum").to_rclone_config()["CheckSum"] is True
    ignore = make_job(compare_method="ignore_existing").to_rclone_config()
    assert ignore["IgnoreExisting"] is True


def test_optional_flags():
    cfg = make_job(fast_list=True, bwlimit="10M").to_rclone_config()
    assert cfg["FastList"] is True
    assert cfg["BwLimit"] == "10M"


def test_extra_flags_are_merged():
    cfg = make_job(extra_flags=json.dumps({"MaxDepth": 2})).to_rclone_config()
    assert cfg["MaxDepth"] == 2


def test_filter_only_when_excludes_present():
    assert make_job().to_rclone_filter() is None
    job = make_job(exclude_patterns=json.dumps(["*.tmp"]))
    assert job.to_rclone_filter() == {"ExcludeRule": ["*.tmp"]}
