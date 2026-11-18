import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_cloud_runtime import dependency_cached, owned_name, percentile, source_rebuilt
import verify_cloud_runtime


def test_cache_probe_requires_cached_dependencies_and_rebuilt_source():
    log = "#12 [builder 4/4] RUN /opt/venv/bin/pip install --require-hashes\n#12 CACHED\n#14 [runtime 5/6] COPY app/canopy/static/ ./canopy/static/\n#14 DONE 0.1s\n"
    assert dependency_cached(log)
    assert source_rebuilt(log)
    assert not dependency_cached(log.replace("#12 CACHED", "#12 DONE 0.2s"))
    assert not source_rebuilt(log.replace("#14 DONE 0.1s", "#14 CACHED"))


def test_cleanup_rejects_names_outside_fixture_namespace():
    owned_name("fixture-api", "fixture")
    with pytest.raises(ValueError):
        owned_name("canopy-postgres", "fixture")
    with pytest.raises(ValueError):
        owned_name("fixtureother-api", "fixture")


def test_percentiles_use_nearest_rank():
    assert percentile([], 0.95) is None
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([4, 1, 2], 0.5) == 2


@pytest.mark.parametrize("unit", ["hours", "Hrs"])
def test_public_price_catalog_accepts_documented_hour_units(monkeypatch, unit):
    products = {}
    terms = {}
    for sku, usage, price in [
        ("cpu", "EUW2-Fargate-vCPU-Hours:perCPU", "0.04656"),
        ("memory", "EUW2-Fargate-GB-Hours", "0.00511"),
    ]:
        products[sku] = {"attributes": {"usagetype": usage}}
        terms[sku] = {
            "term": {
                "priceDimensions": {
                    "rate": {
                        "unit": unit,
                        "pricePerUnit": {"USD": price},
                    }
                }
            }
        }
    payload = json.dumps({"products": products, "terms": {"OnDemand": terms}}).encode()
    monkeypatch.setattr(verify_cloud_runtime.HTTP, "open", lambda *a, **kw: io.BytesIO(payload))
    prices = verify_cloud_runtime.public_fargate_prices()
    assert prices["linux_x86_vcpu_hour"] == 0.04656
    assert prices["linux_x86_gib_hour"] == 0.00511


@pytest.mark.parametrize("backend", ["postgres", "local", "dynamodb", ""])
def test_cloud_selector_rejects_unsupported_backends(monkeypatch, backend):
    # No connection or real environment values: inject a controlled backend and fake store.
    module = ModuleType("canopy.postgres")
    marker = object()
    module.PostgresStore = lambda: marker
    monkeypatch.setitem(sys.modules, "canopy.postgres", module)
    monkeypatch.setenv("BACKEND", backend)
    path = Path(__file__).resolve().parents[1] / "docker/cloud-api/storage.py"
    spec = importlib.util.spec_from_file_location("cloud_storage_fixture", path)
    selector = importlib.util.module_from_spec(spec)
    if backend == "postgres":
        spec.loader.exec_module(selector)
        assert selector.store() is marker
    else:
        with pytest.raises(RuntimeError, match="requires BACKEND=postgres"):
            spec.loader.exec_module(selector)
