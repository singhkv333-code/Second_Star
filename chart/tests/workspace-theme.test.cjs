const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('../../app/node_modules/jsdom');
const source = fs.readFileSync(require.resolve('../js/workspace-theme.js'), 'utf8');
const themeSource = fs.readFileSync(require.resolve('../js/theme.js'), 'utf8');
const KEY = 'pivot-workspace-colors-v1';
function fixture({dark = false, stored, chart = false, legacy} = {}) {
  const dom = new JSDOM('<html><head><script data-surface="'+(chart?'chart':'shell')+'"></script></head><body><button id="profile">Profile</button></body></html>', {url:'https://pivot.test', runScripts:'outside-only'});
  const w = dom.window;
  w.matchMedia = () => ({matches:dark});
  Object.defineProperty(w.document,'currentScript',{value:w.document.querySelector('script')});
  if(stored) w.localStorage.setItem(KEY, stored);
  if(legacy) w.localStorage.setItem('pivot-theme',legacy);
  w.eval(source);
  return {w, dom, api:w.PivotWorkspaceTheme, css:k=>w.document.documentElement.style.getPropertyValue('--'+k)};
}
function lum(c) { const v=c.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4); return v[0]*.2126+v[1]*.7152+v[2]*.0722; }
function contrast(a,b) { return (Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05); }
function press(w,selector) { w.document.querySelector(selector).click(); }
function change(w,selector,value) { const el=w.document.querySelector(selector); el.value=value; el.dispatchEvent(new w.Event('change',{bubbles:true})); }
test('eight distinct paired palettes provide readable chart and frame text', () => {
  const f=fixture();
  assert.equal(f.api.presets.length,8);
  for(const t of f.api.presets) {
    assert.notEqual(t.surface,t.chrome);
    assert.equal(f.api.setTheme(t),true);
    assert.equal(f.css('bg-card'),t.surface);
    assert.equal(f.css('shell-chrome'),t.chrome);
    assert.ok(contrast(t.surface,f.api.chartPalette().axisText)>=4.5,t.name);
    assert.ok(contrast(t.chrome,f.css('workspace-chrome-ink'))>=4.5,t.name+' frame');
  }
  for(const surface of ['#777777','#747474','#eeeeee','#010101','#ac218d']) {
    f.api.setTheme({...f.api.presets[0],surface});
    assert.ok(contrast(surface,f.api.chartPalette().axisText)>=4.5);
  }
  f.dom.window.close();
});
test('migrates an explicit old appearance, then persists independently of the OS', () => {
  const f=fixture({dark:true,legacy:'light'});
  assert.equal(f.api.getTheme().id,'ivory');
  f.api.setTheme(f.api.presets[5]);
  const next=fixture({dark:true,stored:f.w.localStorage.getItem(KEY)});
  assert.equal(next.api.getTheme().id,'dune');
  assert.equal(next.css('bg-card'),'#faf7ef');
  f.dom.window.close(); next.dom.window.close();
});
test('rejects malformed palettes, CSS injection and corrupt persistence', () => {
  const f=fixture({stored:'{bad'});
  const original=f.api.getTheme();
  for(const patch of [{surface:'red;display:none'}, {chrome:'#fff'}, {id:'../x'}, {finish:'glow'}, {name:''}]) {
    assert.equal(f.api.setTheme({...original,...patch}),false);
    assert.equal(f.api.getTheme().surface,original.surface);
  }
  f.dom.window.close();
});
test('custom copies survive reload and names are rendered as text', () => {
  const f=fixture(); f.api.setTheme({...f.api.presets[3],surface:'#101829',finish:'grain'});
  f.api.save('<img src=x>');
  const next=fixture({stored:f.w.localStorage.getItem(KEY)});
  assert.equal(next.api.getState().saved.length,1);
  assert.equal(next.api.getTheme().surface,'#101829');
  next.api.openPicker(next.w.document.querySelector('#profile'));
  assert.equal(next.w.document.querySelectorAll('.workspace-colors img').length,0);
  assert.ok(next.w.document.querySelector('.workspace-palettes').textContent.includes('<img src=x>'));
  next.api.remove(next.api.getTheme().id);
  assert.equal(next.api.getState().saved.length,0);
  assert.equal(next.api.getTheme().id,'ivory');
  f.dom.window.close(); next.dom.window.close();
});
test('picker edits colors, handles invalid hex, resets and saves a named copy', () => {
  const f=fixture(),w=f.w; f.api.openPicker(w.document.querySelector('#profile'));
  press(w,'[data-palette="nord"]');
  change(w,'[data-hex="surface"]','#202733');
  assert.equal(f.api.getTheme().surface,'#202733');
  change(w,'[data-hex="surface"]','oops');
  assert.equal(f.api.getTheme().surface,'#202733');
  assert.match(w.document.querySelector('[role="status"]').textContent,/six-digit/);
  press(w,'[data-reset]'); assert.equal(f.api.getTheme().surface,'#2e3440');
  press(w,'[data-finish="wash"]'); assert.equal(f.api.getTheme().finish,'wash');
  assert.match(f.css('workspace-finish-image'),/gradient/);
  w.document.querySelector('[aria-label="Palette name"]').value='My desk'; press(w,'[data-save]');
  assert.equal(f.api.getState().saved.length,1);
  press(w,'[data-delete]'); assert.equal(f.api.getTheme().id,'ivory');
  f.dom.window.close();
});
test('chooser fits short windows, closes with Escape and returns keyboard focus', () => {
  const f=fixture(); Object.defineProperty(f.w,'innerWidth',{value:375}); Object.defineProperty(f.w,'innerHeight',{value:420});
  const anchor=f.w.document.querySelector('#profile'); anchor.focus(); f.api.openPicker(anchor);
  const dialog=f.w.document.querySelector('[role="dialog"]');
  assert.ok(parseInt(dialog.style.top)+parseInt(dialog.style.maxHeight)<=420);
  f.w.document.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(f.w.document.querySelector('[role="dialog"]'),null);
  assert.equal(f.w.document.activeElement,anchor);
  f.dom.window.close();
});
test('live chart palette changes notify all chart subscribers even within the same contrast mode', () => {
  const f=fixture({chart:true}); const Theme=f.w.eval(themeSource+'\n;Theme;'); Theme.init();
  const seen=[]; Theme.onChange((mode,palette)=>seen.push({mode,bg:palette.chartBg,up:palette.up}));
  f.api.setTheme(f.api.presets[2]); f.api.setTheme(f.api.presets[3]);
  assert.equal(seen.length,2); assert.equal(seen[0].mode,seen[1].mode);
  assert.equal(Theme.c('chartBg'),'#141d2e'); assert.equal(Theme.c('up'),'#57c4ad');
  assert.equal(f.css('background'),'#141d2e');
  assert.notEqual(f.css('border'),f.css('shell-chrome'));
  f.dom.window.close();
});
test('colors remain usable when browser storage is blocked', () => {
  const f=fixture(); const proto=Object.getPrototypeOf(f.w.localStorage); const old=proto.setItem;
  proto.setItem=()=>{throw new Error('blocked');};
  f.api.openPicker(f.w.document.querySelector('#profile')); f.api.setTheme(f.api.presets[4]);
  assert.equal(f.css('bg-card'),'#182824');
  assert.match(f.w.document.querySelector('[role="status"]').textContent,/storage is unavailable/);
  proto.setItem=old; f.dom.window.close();
});

test('circle swatches show the pair and valid hex input previews before blur', () => {
  const f=fixture(); f.api.openPicker(f.w.document.querySelector('#profile'));
  assert.equal(f.w.document.querySelectorAll('.workspace-pair').length,8);
  assert.equal(f.w.document.querySelectorAll('.workspace-palettes svg').length,0);
  assert.equal(f.w.document.querySelectorAll('.workspace-pair i').length,16);
  const input=f.w.document.querySelector('[data-hex="surface"]');
  input.value='#202733'; input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
  assert.equal(f.api.getTheme().surface,'#202733');
  assert.equal(f.css('shell-sidebar-bg'),f.api.getTheme().chrome);
  press(f.w,'[data-finish="grain"]');
  assert.equal(f.api.chartPalette().canvasBg,'transparent');
  assert.equal(f.api.chartPalette().chartBg,'#202733');
  press(f.w,'[data-finish="flat"]');
  assert.equal(f.api.chartPalette().canvasBg,'#202733');
  f.dom.window.close();
});
