#!/usr/bin/env python3
"""Exercise the actual console/guide in Chromium with synthetic read-only evidence.

No provider or owner-state access. Run after installing the test-only Playwright
package and Chromium. Set GUIDE_BROWSER_EXECUTABLE to use system Chromium.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

from ocpf_post import observer_server


def fixture() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    names = ['sources', 'observer', 'buffer', 'work', 'editorial', 'vault', 'admission', 'inventory',
             'service', 'scheduler', 'run_due', 'learning', 'measure_24', 'measure_72', 'measure_168',
             'readback', 'x', 'threads', 'linkedin', 'engagement', 'reply_worker']
    return {
        'schema_version': 1, 'consequence': 'READ_ONLY', 'local_only': True,
        'revision': 'guide-browser-fixture-one', 'observed_at': now,
        'runtime': {'cli_version': '0.28.29 test fixture', 'git_commit_sha': 'fixture-owner-operations'},
        'state_integrity': {'status': 'observed', 'critical_ledger_status': 'observed', 'invalid_count': 0},
        'work': {'status': 'observed', 'items': [], 'summary': {}},
        'portfolio': {'status': {'release_pacing': {'accounts': [
            {'provider': 'x', 'normal_originals_per_day': 24, 'hard_daily_ceiling': 100},
            {'provider': 'threads', 'normal_originals_per_day': 24, 'hard_daily_ceiling': 100},
            {'provider': 'linkedin', 'normal_originals_per_day': 20, 'hard_daily_ceiling': 100},
        ]}}},
        'activity': {
            'active_schedules': [{
                'schedule_id': 'sch-fixture', 'status': 'scheduled', 'campaign': 'PAS-TEST-1',
                'provider': 'x', 'account_id': '123', 'run_at': now,
                'project': 'proof-and-state', 'source_type': 'owner_approved', 'vault_id': 'proof-and-state-gtm',
            }],
            'recent_receipts': [],
            'today': {
                'date': now[:10], 'timezone': 'Europe/London', 'provider_counts': {'x': 1},
                'effects': [{
                    'campaign': 'PAS-TEST-1', 'provider': 'x', 'project': 'proof-and-state',
                    'status': 'published_verified', 'effective_verified': True, 'published_at': now,
                    'post_id': 'fixture-post', 'url': 'https://example.invalid/post',
                    'source_type': 'owner_approved', 'vault_id': 'proof-and-state-gtm',
                }],
            },
        },
        'engagement': {
            'counts': {'pending': 1, 'drafted': 0}, 'waiting_over_24h': 0, 'nested_pending': 0,
            'worker': {'enabled': True, 'model_ready': True, 'provider_mode_counts': {'x:automatic': 1}},
        },
        'replenishment': {'campaigns': [{
            'campaign': 'PAS-TEST-1', 'project': 'proof-and-state',
            'source_type': 'owner_approved', 'vault_id': 'proof-and-state-gtm',
        }]},
        'timers': {'status': 'observed', 'timers': ['run-due', 'refill', 'collection', 'replies']},
        'projection_timing_ms': {'total': 1250.0, 'portfolio': 720.0},
        'capabilities': {'status': 'observed', 'core_status': 'observed', 'capabilities': [], 'core_blockers': [], 'activated_optional_blockers': []},
        'admission': {'mode': 'open', 'metrics': {'eligible_unreserved': 5}, 'thresholds': {}},
        'operator': {'stages': [{'id': key, 'status': 'observed', 'value': 1} for key in ('supply','admission','automatic_schedule','execute','provider','verify')],
                     'reserve': {'route_count': 49, 'available_items': 31, 'status_counts': {'empty': 32, 'emergency': 16, 'fallback': 1}},
                     'diagnosis': {'code': 'supply_empty', 'title': 'Inspect the supply reserve', 'tone': 'warn'}},
        'execution': {'schema_version': 2, 'nodes': [{'id': key, 'label': key.replace('_', ' ').title(), 'value': 1, 'status': 'observed', 'kind': 'stage', 'metric': 'fixture'} for key in names],
                      'edges': [{'source': 'sources', 'target': 'observer', 'mode': 'state'}, {'source': 'admission', 'target': 'inventory', 'mode': 'state'}],
                      'events': [], 'intent': {'plan': [], 'planned_count': 0}},
        'acceptance': {'sections': {}}, 'collection': {'stages': []}, 'machine': {'flows': []},
    }


def main() -> int:
    from playwright.sync_api import sync_playwright
    artifacts = Path(os.environ.get('GUIDE_BROWSER_ARTIFACTS') or tempfile.mkdtemp(prefix='post-once-guide-browser-'))
    artifacts.mkdir(parents=True, exist_ok=True)
    data = fixture()
    requests = []
    errors = []
    results = []
    with patch.object(observer_server, 'build_snapshot', return_value={}), \
         patch.object(observer_server, 'build_execution_snapshot', side_effect=lambda **_: deepcopy(data)):
        server = observer_server.ConsoleServer(('127.0.0.1', 0), observer_server.ConsoleHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as playwright:
                options = {'headless': True, 'args': ['--no-sandbox']}
                if os.environ.get('GUIDE_BROWSER_EXECUTABLE'):
                    options['executable_path'] = os.environ['GUIDE_BROWSER_EXECUTABLE']
                browser = playwright.chromium.launch(**options)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page = context.new_page()
                page.on('request', lambda request: requests.append({'method': request.method, 'url': request.url}))
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='domcontentloaded')
                page.wait_for_function("document.querySelector('#systemDrawer')?.dataset.guideMounted === 'true'")
                page.wait_for_function("typeof model !== 'undefined' && model.snapshot?.guide")
                page.get_by_role('heading', name='Owner operations').wait_for()
                assert page.locator('#ownerTodayCount').inner_text() == '1 published'
                assert 'Proof And State' in page.locator('#ownerTodayList').inner_text()
                assert 'Vault · proof-and-state-gtm' in page.locator('#ownerTodayList').inner_text()
                assert 'X automatic 1' in page.locator('#ownerConversations').inner_text()
                results.append('owner home renders publication origin, booking, pacing, conversation mode and runtime evidence')

                for width in (390, 768, 1440):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.wait_for_timeout(50)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    page.locator('#ownerHome').screenshot(path=str(artifacts / f'owner-home-{width}.png'))
                results.append('owner home has no horizontal overflow at 390/768/1440px; screenshots captured')
                assert not page.locator('#systemDrawerWrap').evaluate("e => e.classList.contains('open')")
                width_before = page.locator('#map').bounding_box()['width']
                page.get_by_role('button', name='Open Guide and doctor').click()
                page.get_by_role('heading', name='Check what’s ready now and what will last').wait_for()
                assert page.locator('#map').bounding_box()['width'] == width_before
                assert page.locator('#heading').evaluate("e => !!e.closest('[inert]')")
                assert page.evaluate('document.activeElement.id') == 'systemHeading'
                results.append('opens only on request; canvas geometry unchanged; focus and inert background')

                for width in (390, 768, 1440):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.wait_for_timeout(50)
                    assert page.locator('#systemDrawer').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
                    page.locator('#systemDrawer').screenshot(path=str(artifacts / f'guide-next-{width}.png'))
                results.append('390/768/1440px drawers do not overflow; real-page screenshots captured')

                page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async()=>{throw new Error('denied')}}})")
                page.get_by_role('button', name='Copy inspection command').first.click()
                assert 'Clipboard access is unavailable' in page.locator('.po-status').inner_text()
                assert './ocpf-post replenish status' in page.evaluate('window.getSelection().toString()')
                results.append('clipboard failure provides manual copy without claiming execution')

                page.get_by_role('tab', name='Explore', exact=True).click()
                page.get_by_role('button', name='What was published today, and where did it come from?', exact=True).click()
                assert 'bash scripts/owner-daily.sh' in page.locator('#po-panel-explore').inner_text()
                assert 'Creates report files only' in page.locator('#po-panel-explore').inner_text()
                page.get_by_role('tab', name='All commands', exact=True).click()
                page.locator('#po-guide-level').select_option('advanced')
                search = page.get_by_role('searchbox'); search.fill('publish')
                page.locator('.po-library-row summary').first.click()
                assert './ocpf-post help' in page.locator('#po-panel-commands').inner_text()
                assert '--live' not in '\n'.join(page.locator('#po-panel-commands .po-command').all_text_contents())
                search.fill('no-such-command-xyz')
                assert 'No matching command' in page.locator('#po-panel-commands').inner_text()
                search.fill('vault')
                assert search.input_value() == 'vault'
                results.append('curiosity report; advanced search; no mutation command copied; unknown search is explicit')

                # Update the real composite server fixture while the reader is in the library.
                data['revision'] = 'guide-browser-fixture-two'
                data['operator']['reserve']['status_counts'] = {}
                data['observed_at'] = datetime.now(timezone.utc).isoformat()
                server._snapshot_cached_at = 0
                page.get_by_role('button', name='Use the latest evidence', exact=True).wait_for(timeout=15000)
                assert search.input_value() == 'vault'
                assert page.evaluate('document.activeElement.id') == 'po-command-search'
                page.get_by_role('button', name='Use the latest evidence', exact=True).click()
                page.get_by_role('tab', name='Next steps', exact=True).click()
                assert 'Nothing urgent in the checks shown' in page.locator('#po-panel-next').inner_text()
                results.append('new evidence preserves search/focus; explicit refresh removes outdated suggestion without repair claim')

                page.get_by_role('tab', name='Next steps', exact=True).focus()
                page.keyboard.press('ArrowRight')
                assert page.get_by_role('tab', name='Explore', exact=True).get_attribute('aria-selected') == 'true'
                page.keyboard.press('Escape')
                page.wait_for_function("!document.querySelector('#systemDrawerWrap').classList.contains('open')")
                assert page.evaluate('document.activeElement.id') == 'systemButton'
                assert not page.locator('#heading').evaluate("e => !!e.closest('[inert]')")
                # Stage shortcuts are inside the canvas workspace's closed disclosure.
                page.locator('.pipeline-disclosure > summary').click()
                page.locator('#opStageSupply').press('Enter')
                page.get_by_role('heading', name='Why is reserve low when there is content?').wait_for()
                page.keyboard.press('Escape')
                assert page.evaluate('document.activeElement.id') == 'opStageSupply'
                results.append('keyboard tabs, Escape, background restoration and contextual focus return')

                assert all(request['method'] == 'GET' for request in requests)
                assert all(request['url'].startswith(f'http://127.0.0.1:{server.server_port}/') for request in requests)
                assert sum('/api/stream' in request['url'] for request in requests) == 1
                assert not errors, errors
                results.append('all traffic is local GET; one existing SSE connection; zero page errors')
                context.close(); browser.close()
        finally:
            server.shutdown(); server.server_close()
    report = {'status': 'passed', 'checks': results, 'requests': requests, 'page_errors': errors,
              'boundary': 'Synthetic evidence on the repository execution page, not owner-host or live-provider acceptance.'}
    (artifacts / 'browser-results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
