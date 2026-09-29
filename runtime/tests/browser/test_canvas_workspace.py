"""Real Chromium UI regressions; all snapshots and SSE events are synthetic.

Run with:
  python -m pip install playwright
  python -m playwright install chromium
  python tests/browser/test_canvas_workspace.py

For a preinstalled browser set PLAYWRIGHT_CHROMIUM_EXECUTABLE.
No HTTP server, credentials, publisher, daemon or real state files are used.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / 'src/ocpf_post/ui/execution.html').read_text(encoding='utf-8')
FIXTURE = json.loads(Path(__file__).with_name('canvas_snapshot.json').read_text(encoding='utf-8'))


class CanvasWorkspaceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        kwargs = {'headless': True}
        executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        if executable:
            kwargs['executable_path'] = executable
        cls.browser = cls.playwright.chromium.launch(**kwargs)
        print(f'Browser: Chromium {cls.browser.version}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1468, 'height': 906}, reduced_motion='reduce')
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/*', lambda route: route.abort())
        self.page.evaluate('''fixture => {
          window.__fixture = fixture;
          window.__requests = [];
          window.fetch = async (url, options = {}) => {
            window.__requests.push({url, method: options.method || 'GET'});
            if (url !== '/api/snapshot') throw new Error('Unexpected endpoint: ' + url);
            return {ok:true, json:async()=>structuredClone(fixture)};
          };
          window.EventSource = class {
            constructor(url) { this.url=url; this.listeners={}; window.__stream=this; }
            addEventListener(name, handler) { this.listeners[name]=handler; }
            close() {}
            push(value) { this.listeners.snapshot({data:JSON.stringify(value)}); }
          };
        }''', FIXTURE)
        # Render the production HTML directly; transport is substituted, not UI code.
        self.page.set_content(HTML)
        self.page.wait_for_function('model.initialised && document.querySelectorAll("#nodeLayer .node").length === 21')
        self.settle()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], 'Browser raised JavaScript errors')

    def settle(self):
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    def view(self):
        return self.page.evaluate('({zoom:model.zoom,x:model.viewX,y:model.viewY,mode:camera.mode})')

    def assert_node_visible(self, node_id):
        rects = self.page.evaluate('''id => {
          const r = document.querySelector('#map').getBoundingClientRect();
          const n = document.querySelector('#node-' + id + ' .body').getBoundingClientRect();
          return {map:{left:r.left,right:r.right,top:r.top,bottom:r.bottom},node:{left:n.left,right:n.right,top:n.top,bottom:n.bottom}};
        }''', node_id)
        a, n = rects['map'], rects['node']
        self.assertGreaterEqual(n['left'], a['left'] + 4, node_id)
        self.assertGreaterEqual(n['top'], a['top'] + 4, node_id)
        self.assertLessEqual(n['right'], a['right'] - 4, node_id)
        self.assertLessEqual(n['bottom'], a['bottom'] - 4, node_id)

    def test_canvas_height_and_no_document_overflow(self):
        for width, height in [(1468,906),(1280,720),(1024,768),(734,453),(390,844)]:
            with self.subTest(viewport=(width,height)):
                self.page.set_viewport_size({'width':width,'height':height})
                self.settle()
                dimensions = self.page.evaluate('({overflow:document.documentElement.scrollWidth-innerWidth,height:document.querySelector("#map").getBoundingClientRect().height})')
                self.assertLessEqual(dimensions['overflow'], 1)
                self.assertGreaterEqual(dimensions['height'], 280)
                if width == 1468:
                    self.assertGreaterEqual(dimensions['height'], 600)

    def test_overview_keeps_every_node_inside_padding_after_resize(self):
        for width, height in [(1468,906),(1280,720),(390,844)]:
            self.page.set_viewport_size({'width':width,'height':height})
            self.page.locator('#fitView').click()
            self.settle()
            for node in FIXTURE['execution']['nodes']:
                self.assert_node_visible(node['id'])

    def test_zoom_is_anchored_to_pointer(self):
        self.page.locator('#readableView').click()
        box = self.page.locator('#map').bounding_box()
        x, y = round(box['x']+box['width']*.57), round(box['y']+box['height']*.55)
        def world():
            return self.page.evaluate('''([x,y])=>{const p=document.querySelector('#map').createSVGPoint();p.x=x;p.y=y;const q=p.matrixTransform(document.querySelector('#map').getScreenCTM().inverse());return {x:q.x,y:q.y};}''',[x,y])
        before = world()
        self.page.mouse.move(x,y)
        self.page.keyboard.down('Control')
        self.page.mouse.wheel(0,-60)
        self.page.keyboard.up('Control')
        self.settle()
        after = world()
        self.assertAlmostEqual(before['x'],after['x'],places=3)
        self.assertAlmostEqual(before['y'],after['y'],places=3)
        self.assertGreater(self.view()['zoom'],1.25)

    def test_drag_pans_without_selecting_a_node_and_respects_bounds(self):
        self.page.locator('#readableView').click()
        before=self.view()
        box=self.page.locator('#map').bounding_box()
        x,y=box['x']+box['width']/2,box['y']+box['height']/2
        self.page.mouse.move(x,y)
        self.page.mouse.down()
        self.page.mouse.move(x+130,y+65,steps=8)
        self.page.mouse.up()
        self.settle()
        after=self.view()
        self.assertAlmostEqual(after['x'],before['x']-130/1.25,places=2)
        self.assertAlmostEqual(after['y'],before['y']-65/1.25,places=2)
        self.assertIsNone(self.page.evaluate('model.selected'))
        self.assertFalse(self.page.locator('#map').evaluate("el=>el.classList.contains('is-panning')"))
        # Clamp at the horizontal edge while retaining the chosen stage's vertical centre.
        self.page.locator('#stageJump').select_option('run_due')
        self.settle()
        self.page.evaluate('panView(1e6,0)')
        self.assert_node_visible('run_due')
        self.page.locator('#stageJump').select_option('reply_worker')
        self.settle()
        self.page.evaluate('panView(0,1e6)')
        self.assert_node_visible('reply_worker')

    def test_all_stage_jump_targets_are_readable_and_reachable(self):
        for node in FIXTURE['execution']['nodes']:
            with self.subTest(node=node['id']):
                self.page.locator('#stageJump').select_option(node['id'])
                self.settle()
                self.assert_node_visible(node['id'])
                self.assertEqual(self.page.locator('#inspectTitle').inner_text(),node['label'])
                sizes=self.page.evaluate('''id=>{const el=document.querySelector('#node-'+id+' .name');const metric=document.querySelector('#node-'+id+' .metric');return {name:parseFloat(getComputedStyle(el).fontSize)*el.getScreenCTM().a,metric:parseFloat(getComputedStyle(metric).fontSize)*metric.getScreenCTM().a,detail:parseFloat(getComputedStyle(document.querySelector('#inspectText')).fontSize)};}''',node['id'])
                self.assertGreaterEqual(sizes['name'],20)
                self.assertGreaterEqual(sizes['metric'],15)
                self.assertGreaterEqual(sizes['detail'],14)

    def test_inspector_does_not_overlap_the_map_or_its_own_blocks(self):
        self.page.locator('#stageJump').select_option('readback');self.settle()
        rects=self.page.evaluate('''()=>Object.fromEntries(['#map','.inspector','.now','#contextPanel'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return [s,{left:r.left,right:r.right,top:r.top,bottom:r.bottom}]}))''')
        self.assertGreaterEqual(rects['#contextPanel']['left'],rects['#map']['right'])
        self.assertGreaterEqual(rects['.now']['top'],rects['.inspector']['bottom']-1)
        self.page.locator('#closeDetails').click();self.settle()
        self.assertTrue(self.page.locator('#contextPanel').is_hidden())
        self.assertEqual(self.page.locator('#detailsToggle').get_attribute('aria-expanded'),'false')

    def test_focus_mode_is_permission_independent_and_escape_restores(self):
        before=self.page.locator('#map').bounding_box()['height']
        self.page.evaluate('''()=>{document.documentElement.requestFullscreen=()=>{throw new Error('Permission denied')};}''')
        self.page.locator('#fullScreen').click();self.settle()
        self.assertTrue(self.page.evaluate('camera.focus'))
        self.assertIsNone(self.page.evaluate('document.fullscreenElement'))
        self.assertGreater(self.page.locator('#map').bounding_box()['height'],before)
        self.assertEqual(self.page.locator('#execution-visualizer').get_attribute('role'),'dialog')
        self.assertTrue(self.page.locator('.command-bar').evaluate('el=>el.inert'))
        self.page.keyboard.press('Escape');self.settle()
        self.assertFalse(self.page.evaluate('camera.focus'))
        self.assertFalse(self.page.locator('.command-bar').evaluate('el=>el.inert'))
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'fullScreen')

    def test_keyboard_and_minimap_navigation(self):
        self.page.locator('#map').focus()
        self.page.keyboard.press('r')
        before=self.view()
        self.page.keyboard.press('ArrowRight')
        self.assertGreater(self.view()['x'],before['x'])
        self.page.keyboard.press('0')
        self.assertEqual(self.view()['mode'],'overview')
        self.page.locator('#miniMap').click(position={'x':95,'y':22})
        self.assertGreaterEqual(self.view()['zoom'],1.25)
        self.assertGreater(self.view()['x'],870)

    def test_live_snapshot_preserves_camera_and_uses_fresh_selected_values(self):
        self.page.locator('#readableView').click()
        self.page.evaluate('panView(30,20)')
        before=self.view()
        updated=copy.deepcopy(FIXTURE)
        next(n for n in updated['execution']['nodes'] if n['id']=='readback')['value']=32
        updated['revision']='fixture-updated'
        self.page.evaluate('value=>window.__stream.push(value)',updated)
        self.settle()
        self.assertEqual(self.view(),before)
        self.assertEqual(self.page.locator('#node-readback .value').text_content(),'32')
        self.page.locator('#node-readback').dispatch_event('click')
        self.assertIn('32 verified effects',self.page.locator('#inspectMeta').inner_text())

    def test_native_mouse_click_selects_without_moving_the_target(self):
        self.page.locator('#readableView').click()
        before=self.view()
        self.page.locator('#node-measure_72').click()
        self.settle()
        self.assertEqual(self.page.evaluate('model.selected.value.id'),'measure_72')
        self.assertEqual(self.view()['x'],before['x'])
        self.assertEqual(self.view()['y'],before['y'])
        self.assertFalse(self.page.locator('#contextPanel').is_hidden())

    def test_already_open_inspector_refreshes_without_reopening_a_closed_panel(self):
        self.page.locator('#stageJump').select_option('readback');self.settle()
        before=self.view()
        updated=copy.deepcopy(FIXTURE)
        next(n for n in updated['execution']['nodes'] if n['id']=='readback')['value']=41
        self.page.evaluate('value=>window.__stream.push(value)',updated);self.settle()
        self.assertIn('41 verified effects',self.page.locator('#inspectMeta').inner_text())
        self.assertEqual(self.view(),before)
        self.page.locator('#closeDetails').click();self.settle()
        self.page.evaluate('value=>window.__stream.push(value)',FIXTURE);self.settle()
        self.assertTrue(self.page.locator('#contextPanel').is_hidden())

    def test_lost_window_focus_cleans_up_pointer_tracking(self):
        self.page.locator('#map').scroll_into_view_if_needed()
        box=self.page.locator('#map').bounding_box()
        self.page.mouse.move(box['x']+100,box['y']+100)
        self.page.mouse.down()
        self.assertEqual(self.page.evaluate('camera.pointers.size'),1)
        self.page.evaluate("window.dispatchEvent(new Event('blur'))")
        self.assertEqual(self.page.evaluate('camera.pointers.size'),0)
        self.assertFalse(self.page.locator('#map').evaluate("el=>el.classList.contains('is-panning')"))
        self.page.mouse.up()

    def test_history_replay_and_return_live_keep_evidence_and_read_only_boundary(self):
        self.assertTrue(self.page.locator('#historyPanel').is_hidden())
        self.page.locator('#historyToggle').click();self.settle()
        self.page.locator('#replayBtn').click()
        self.assertEqual(self.page.evaluate('model.mode'),'replay')
        self.assertFalse(self.page.evaluate('model.playing'),'Reduced motion should not autoplay')
        self.page.locator('#timeline').evaluate("el=>{el.value='450';el.dispatchEvent(new Event('input',{bubbles:true}));}")
        self.assertEqual(self.page.evaluate('model.playhead'),450)
        self.page.locator('#liveBtn').click()
        self.assertEqual(self.page.evaluate('model.mode'),'live')
        self.assertEqual(self.page.evaluate('model.events.length'),len(FIXTURE['execution']['events']))
        self.assertEqual(self.page.evaluate('window.__requests'),[{'url':'/api/snapshot','method':'GET'}])
        self.assertEqual(self.page.evaluate('window.__stream.url'),'/api/stream')

    def test_mobile_details_and_short_focus_have_no_horizontal_overflow(self):
        self.page.set_viewport_size({'width':390,'height':844})
        self.page.locator('#stageJump').select_option('sources');self.settle()
        self.assert_node_visible('sources')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
        rects=self.page.evaluate('''()=>{const a=document.querySelector('#map').getBoundingClientRect(),b=document.querySelector('#contextPanel').getBoundingClientRect();return {mapBottom:a.bottom,detailsTop:b.top};}''')
        self.assertGreaterEqual(rects['detailsTop'],rects['mapBottom'])
        self.page.locator('#closeDetails').click()
        self.page.locator('#fullScreen').click();self.settle()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
        self.page.set_viewport_size({'width':734,'height':453});self.settle()
        self.assertGreaterEqual(self.page.locator('#map').bounding_box()['height'],160)
        self.page.keyboard.press('Escape')

    def test_touch_pinch_and_cancel_leave_no_stuck_gesture(self):
        self.page.locator('#readableView').click()
        before=self.view()['zoom']
        box=self.page.locator('#map').bounding_box()
        x,y=box['x']+box['width']/2,box['y']+box['height']/2
        client=self.context.new_cdp_session(self.page)
        def touch(kind,points):
            client.send('Input.dispatchTouchEvent',{'type':kind,'touchPoints':[{'x':px,'y':py,'id':i} for i,(px,py) in enumerate(points)]})
        touch('touchStart',[(x-40,y),(x+40,y)])
        touch('touchMove',[(x-75,y),(x+75,y)])
        touch('touchEnd',[]);self.settle()
        self.assertGreater(self.view()['zoom'],before)
        self.assertEqual(self.page.evaluate('camera.pointers.size'),0)
        self.assertFalse(self.page.locator('#map').evaluate("el=>el.classList.contains('is-panning')"))


if __name__=='__main__':
    unittest.main(verbosity=2)
