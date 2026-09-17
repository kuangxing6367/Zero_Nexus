# -*- coding: utf-8 -*-
"""GraphQL 功能域测试：schema 执行（查询 + 变更），用假 framework 注入 _CTX。"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ariadne 是可选依赖（CI 有意不装，验证缺依赖时优雅跳过）
try:
    import software.extensions.webui.graphql as g
    from software.extensions.webui.graphql import graphql_sync, schema
except ImportError as e:
    print(f"SKIP - ariadne 未安装（可选依赖）: {e}")
    sys.exit(0)


class FakePluginLoader:
    def get_loaded_plugins(self):
        return {'alpha': 1, 'beta': 2}


class FakeServices:
    def all(self):
        return {'scheduler': object(), 'ws_server': object()}


class FakeBus:
    _subscribers = {'tick': [1, 2, 3]}

    def emit(self, *args, **kwargs):
        return None


class FakeFW:
    start_time = 1000.0
    plugin_loader = FakePluginLoader()
    services = FakeServices()
    event_bus = FakeBus()


def test_graphql_queries():
    ctx = types.SimpleNamespace(
        framework=FakeFW(),
        _get_framework_local_version=lambda: "9.9.9",
    )
    g._CTX = ctx

    ok, res = graphql_sync(schema, {
        "query": """
        {
          info { name version uptimeSeconds startTime }
          health
          plugins { name enabled }
          services { name type }
        }
        """,
    })
    assert ok, res
    d = res['data']
    assert d['info']['name'] == 'Zeronus'
    assert d['info']['version'] == '9.9.9'
    assert d['health'] == 'ok'
    assert sorted(p['name'] for p in d['plugins']) == ['alpha', 'beta']
    assert sorted(s['name'] for s in d['services']) == ['scheduler', 'ws_server']
    print("test_api_graphql query: PASS")

    ok2, res2 = graphql_sync(schema, {
        "query": "mutation($p: JSON) { emitEvent(name: \"tick\", payload: $p) { ok listeners } }",
        "variables": {'p': {'a': 1}},
    })
    assert ok2, res2
    assert res2['data']['emitEvent']['ok'] is True
    assert res2['data']['emitEvent']['listeners'] == 3
    print("test_api_graphql mutation: PASS")


if __name__ == '__main__':
    test_graphql_queries()
    print("ALL PASS")
