/* PaperBot WebUI 前端（零构建单页应用）
 * 原生 JS：左侧菜单 → hash 路由 → 各页渲染 + 交互回调。
 * API 前缀由后端注入（window.__WEBUI_PREFIX__），默认 /api。
 */
(function () {
  'use strict';

  // 后端注入的前缀；未注入 / 仍是占位符 / 为空时一律回退到 /api
  var RAW_PREFIX = String(window.__WEBUI_PREFIX__ || '');
  var API =
    RAW_PREFIX && RAW_PREFIX.charAt(0) !== '%' && RAW_PREFIX.indexOf('__') !== 0
      ? RAW_PREFIX
      : '/api';

  var MENU = [
    { id: 'status', label: '状态', icon: '📊' },
    { id: 'config', label: '系统配置', icon: '⚙️' },
    { id: 'permissions', label: '黑白名单', icon: '🚫' },
    { id: 'plugins', label: '插件管理', icon: '🧩' },
    { id: 'llm', label: 'LLM 与记忆', icon: '🤖' },
    { id: 'database', label: '数据库管理', icon: '🗄️' },
    { id: 'commands', label: '命令管理', icon: '⌨️' },
    { id: 'logs', label: '日志', icon: '📜' },
    { id: 'settings', label: '设置', icon: '🛠️' },
  ];

  var SCOPE_LABEL = { global: '全局', group: '群', member: '成员' };
  var LEVEL_COLOR = { 0: 'red', 1: 'grey', 2: 'blue', 3: 'pink' };
  var ROLE_LABEL = { owner: '群主', admin: '管理员', member: '群员' };

  var state = {
    token: localStorage.getItem('paperbot_token') || '',
    db: { name: '', offset: 0, limit: 50, total: 0 },
  };

  /* ---------------------------------------------------------------- 小工具 */
  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function badge(text, color) {
    return '<span class="badge ' + (color || 'grey') + '">' + esc(text) + '</span>';
  }

  function stat(label, value, sub) {
    return (
      '<div class="stat"><div class="label">' + esc(label) + '</div>' +
      '<div class="value">' + esc(value) + '</div>' +
      '<div class="sub">' + esc(sub || '') + '</div></div>'
    );
  }

  // 带进度条的指标（CPU / 内存使用率）
  function meter(label, percent, text) {
    var p = Math.max(0, Math.min(100, Number(percent) || 0));
    var color = p >= 85 ? 'red' : (p >= 60 ? 'amber' : 'green');
    return (
      '<div class="meter"><div class="meter-head"><span>' + esc(label) + '</span>' +
      '<span class="mono">' + esc(text || (p + '%')) + '</span></div>' +
      '<div class="meter-bar"><div class="meter-fill ' + color + '" style="width:' + p + '%"></div></div></div>'
    );
  }

  function kv(pairs) {
    var body = pairs
      .map(function (pair) {
        return '<div class="k">' + esc(pair[0]) + '</div><div>' + pair[1] + '</div>';
      })
      .join('');
    return '<div class="kv">' + body + '</div>';
  }

  function card(title, body, note) {
    return (
      '<div class="card"><div class="card-head"><div>' +
      '<div class="card-title">' + esc(title) + '</div>' +
      (note ? '<div class="card-note">' + note + '</div>' : '') +
      '</div></div><div class="card-body">' + body + '</div></div>'
    );
  }

  function empty(text) {
    return '<div class="empty">' + esc(text) + '</div>';
  }

  function toast(message, kind) {
    var el = document.getElementById('toast');
    el.textContent = message;
    el.className = 'toast show' + (kind ? ' ' + kind : '');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () {
      el.className = 'toast';
    }, 2800);
  }

  function api(path, options) {
    var opts = Object.assign({}, options || {});
    var headers = { 'Content-Type': 'application/json' };
    if (state.token) headers.Authorization = 'Bearer ' + state.token;
    opts.headers = headers;
    return fetch(API + path, opts).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (e) {
          data = text;
        }
        if (!res.ok) {
          var detail = res.statusText;
          if (data && data.detail) {
            detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
          }
          if (res.status === 401) detail += '（请检查右上角的访问令牌）';
          var err = new Error(detail);
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function send(path, method, body) {
    return api(path, {
      method: method,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  // 通用事件绑定助手：`on(scope, '[data-x]', 'click', fn)`（等价 querySelectorAll+forEach）
  function on(scope, selector, event, fn) {
    scope.querySelectorAll(selector).forEach(function (el) {
      el.addEventListener(event, function (ev) {
        fn(el, ev);
      });
    });
  }

  // 通用写操作助手：PUT/POST/DELETE + toast + 成功后回调（默认 render 刷新整页）
  // opts: { ok: 成功提示, after: 成功后的动作(默认 render), err: 失败提示前缀, disabled: 是否临时禁用按钮 }
  function mutate(path, method, body, opts) {
    opts = opts || {};
    var btn = opts.btn || null;
    if (btn) btn.disabled = true;
    return send(path, method, body)
      .then(function (res) {
        var msg = typeof opts.ok === 'function' ? opts.ok(res) : (opts.ok || (res && res.message) || '已保存');
        toast(msg, 'ok');
        if (opts.after) opts.after(res);
        else render();
      })
      .catch(function (err) {
        toast((opts.err || '操作失败') + '：' + err.message, 'err');
        if (opts.after) opts.after(err, true);
      })
      .then(function () {
        if (btn) btn.disabled = false;
      });
  }

  function errorBox(err) {
    var hint =
      err.status === 401
        ? '<br>提示：该实例设置了 WEBUI_TOKEN，请在右上角填写令牌后点「保存令牌」。'
        : '';
    return '<div class="error-box">请求失败：' + esc(err.message || err) + hint + '</div>';
  }

  /* ---------------------------------------------------------------- 状态页 */
  function pageStatus() {
    return api('/status').then(function (d) {
      var bots = d.connection.bots;
      var guard = d.guard;

      var botTable = bots.length
        ? '<div class="table-wrap"><table><thead><tr><th>Bot QQ</th><th>适配器</th><th>连接</th></tr></thead><tbody>' +
          bots
            .map(function (b) {
              return (
                '<tr><td class="mono">' + esc(b.self_id) + '</td><td>' + esc(b.adapter) + '</td><td>' +
                (b.connected ? badge('已连接', 'green') : badge('未连接', 'red')) +
                '</td></tr>'
              );
            })
            .join('') +
          '</tbody></table></div>'
        : empty('当前没有已连接的协议端 Bot');

      var guardBody = kv([
        ['守卫状态', guard.enabled ? badge('已启用', 'green') : badge('已关闭', 'amber')],
        [
          '白名单群',
          guard.groups.length
            ? guard.groups
                .map(function (g) {
                  return badge(g, 'blue');
                })
                .join(' ')
            : badge('空：不向任何目标发消息', 'red'),
        ],
        ['私聊', guard.enabled ? badge('一律拦截', 'grey') : badge('不拦截', 'grey')],
      ]);

      var enabled = d.counts.plugins_enabled === undefined ? d.counts.plugins : d.counts.plugins_enabled;
      var startedAt = Date.parse((d.server.started_at || '').replace(' ', 'T'));
      var tiles =
        '<div class="grid">' +
        stat('服务状态', d.connection.online ? '已连接' : '未连接', d.connection.online ? '协议端在线' : '协议端离线') +
        '<div class="stat" id="tile-uptime"><div class="label">运行时长</div>' +
          '<div class="value" id="uptime-value">' + esc(d.server.uptime_text) + '</div>' +
          '<div class="sub">启动于 ' + esc(d.server.started_at) + '</div></div>' +
        stat('插件', enabled + ' / ' + d.counts.plugins, '已启用 / 总数量') +
        stat('数据库大小', d.storage.size_text, 'SQLite 单文件') +
        '</div>';

      return (
        '<div data-started="' + (d.server.started_at_ts || '') + '">' +
        tiles +
        '<div class="two-col">' +
        '<div>' +
        card('主机资源', '<div id="host-body">' + hostBodyHtml(d.host || {}) + '</div>', 'CPU / 内存 / 本进程 (psutil)，每 5 秒自动刷新') +
        '</div>' +
        '<div>' +
        card('协议端连接', botTable, 'OneBot v11 正向 WebSocket') +
        card('消息输出守卫', guardBody, '开发期防误发的安全网') +
        '</div>' +
        '</div>' +
        card(
          '运行环境',
          kv([
            ['主机时间', '<span id="server-now">' + esc(d.server.now) + '</span>'],
            ['Python / 线程', esc(d.process.python) + ' / ' + d.process.threads],
            ['平台 / PID', esc(d.process.platform) + ' · PID ' + d.process.pid],
            ['WebUI', '<span class="mono">' + esc(d.webui.host) + ':' + esc(d.webui.port) +
              '</span> · ' + (d.webui.token_required ? badge('需令牌', 'green') : badge('未设令牌', 'amber'))],
            ['数据库', '<span class="mono">' + esc(d.storage.database) + '</span>'],
            ['群 / 规则 / 授权', d.counts.groups + ' / ' + d.counts.rules + ' / ' + d.counts.grants],
          ]),
          '进程与存储概览'
        ) +
        '</div>'
      );
    });
  }

  // 主机资源卡片正文（供首次渲染 + 定时局部刷新共用）
  function hostBodyHtml(h) {
    if (!h || !h.available) return empty('未启用主机监控（需安装 psutil）');
    var loadText = '负载 ' + h.load1 + ' / ' + h.load5 + ' / ' + h.load15;
    return (
      meter('CPU 使用率', h.cpu_percent, h.cpu_percent + '%') +
      '<div class="hint" style="margin:2px 0 12px">' + h.cpu_count + ' 核 · ' + esc(loadText) + '</div>' +
      meter('内存使用', h.mem_percent, h.mem_percent + '%') +
      '<div class="hint" style="margin:2px 0 12px">共 ' + esc(h.mem_total_text) +
      ' · 已用 ' + esc(h.mem_used_text) + ' · 空闲 ' + esc(h.mem_free_text) + '</div>' +
      '<div class="kv">' +
      '<div class="k">本进程占用</div><div class="mono">' + esc(h.proc_rss_text) + '</div>' +
      '<div class="k">本进程线程</div><div class="mono">' + esc(h.proc_threads) + '</div>' +
      '</div>'
    );
  }

  // 状态页自动刷新：运行时长本地每秒自增 + 主机指标每 5 秒轮询（离开页面自动停）
  function afterStatus(root) {
    var uptimeEl = root.querySelector('#uptime-value');
    var marker = root.querySelector('[data-started]');
    var startedAt = marker && marker.dataset.started ? Number(marker.dataset.started) : NaN;

    if (!isNaN(startedAt) && uptimeEl) {
      var tick = function () {
        var sec = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
        uptimeEl.textContent = fmtDuration(sec);
      };
      addPageTimer(setInterval(tick, 1000));
      tick();
    }

    addPageTimer(
      setInterval(function () {
        if (!document.getElementById('host-body')) return;
        api('/status')
          .then(function (d) {
            var box = document.getElementById('host-body');
            if (box) box.innerHTML = hostBodyHtml(d.host || {});
            var now = document.getElementById('server-now');
            if (now) now.textContent = d.server.now;
          })
          .catch(function () { /* 静默失败，下次再试 */ });
      }, 5000)
    );
  }

  // 秒数 → 「1 天 2 小时 3 分 4 秒」（与后端 human_duration 口径一致）
  function fmtDuration(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    var d = Math.floor(seconds / 86400);
    var h = Math.floor((seconds % 86400) / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = seconds % 60;
    var parts = [];
    if (d) parts.push(d + ' 天');
    if (h || d) parts.push(h + ' 小时');
    if (m || h || d) parts.push(m + ' 分');
    parts.push(s + ' 秒');
    return parts.join(' ');
  }

  /* -------------------------------------------------------------- 系统配置 */
  /* 通用字段渲染：系统配置（.env）与插件配置项（数据库）共用。
     opts.noMissing    —— 不显示「未在 .env 中显式设置」提示
     opts.missingHint  —— 未配置时的提示文案
     opts.configuredHint —— 已配置时的提示文案 */
  // 选项数超过该值时，给下拉加一个过滤输入框
  var SELECT_SEARCH_THRESHOLD = 8;

  // 一个列表项（标签）：内容 + 删除按钮
  function chipHtml(value) {
    return (
      '<span class="chip" data-chip-value="' + esc(value) + '">' +
      '<span class="chip-text">' + esc(value) + '</span>' +
      '<button type="button" class="chip-x" data-chip-del title="移除">×</button>' +
      '</span>'
    );
  }

  // 收集某个 chips 容器里的值 → 数组
  function collectChips(container) {
    var out = [];
    container.querySelectorAll('.chip').forEach(function (c) {
      out.push(c.dataset.chipValue);
    });
    return out;
  }

  function fieldInput(field, opts) {
    opts = opts || {};
    var key = esc(field.key);
    var common =
      'data-key="' + key + '" data-type="' + esc(field.type) + '" ' +
      (field.secret ? 'data-secret="1" ' : '');
    var control = '';

    if (field.secret) {
      control =
        '<input class="input wide" type="password" ' + common + 'value="" autocomplete="new-password" ' +
        'placeholder="' + (field.configured ? '已配置，留空不修改' : '未配置') + '">';
    } else if (field.type === 'bool') {
      control =
        '<select class="input wide" ' + common + '>' +
        '<option value="true"' + (field.value === true ? ' selected' : '') + '>true（开启）</option>' +
        '<option value="false"' + (field.value === false ? ' selected' : '') + '>false（关闭）</option>' +
        '</select>';
    } else if (field.type === 'list') {
      // 列表型：标签（chips）编辑——输入后点「添加」/回车加入，每项可删
      var listVal = Array.isArray(field.value) ? field.value : (field.value ? [field.value] : []);
      control =
        '<input type="hidden" ' + common + 'value="' + esc(JSON.stringify(listVal)) + '">' +
        '<div class="chips" data-chips="' + key + '">' +
        listVal
          .map(function (v) {
            return chipHtml(v);
          })
          .join('') +
        '<input class="input chip-input" data-chip-input="' + key + '" placeholder="输入后回车 / 点添加">' +
        '<button type="button" class="btn small ghost" data-chip-add="' + key + '">添加</button>' +
        '</div>';
    } else if (field.choices && field.choices.length) {
      var current = field.value === null || field.value === undefined ? '' : String(field.value);
      var clist = field.choices.slice();
      if (current && clist.indexOf(current) < 0) clist.unshift(current);
      var select =
        '<select class="input wide" ' + common + '>' +
        clist
          .map(function (v) {
            return (
              '<option value="' + esc(v) + '"' + (v === current ? ' selected' : '') + '>' + esc(v) + '</option>'
            );
          })
          .join('') +
        '</select>';
      control =
        clist.length > SELECT_SEARCH_THRESHOLD
          ? '<input class="input wide" data-select-filter="' + key + '" placeholder="输入以筛选…">' + select
          : select;
    } else if (field.type === 'text') {
      var text =
        field.value === null || field.value === undefined ? '' : String(field.value);
      control = '<textarea class="input wide" rows="3" ' + common + '>' + esc(text) + '</textarea>';
    } else if (field.type === 'int' || field.type === 'float') {
      var num =
        field.value === null || field.value === undefined ? '' : String(field.value);
      control =
        '<input class="input wide" type="number" step="' + (field.type === 'float' ? 'any' : '1') + '" ' +
        common + 'value="' + esc(num) + '">';
    } else {
      var value =
        field.value === null || field.value === undefined ? '' : String(field.value);
      control = '<input class="input wide" type="text" ' + common + 'value="' + esc(value) + '">';
    }

    var hints = [];
    if (field.hint) hints.push(esc(field.hint));
    if (field.secret) {
      hints.push(field.configured ? '已配置（长度 ' + field.length + '）' : '未配置');
    } else if (field.configured) {
      if (opts.configuredHint) hints.push(opts.configuredHint);
    } else if (opts.missingHint) {
      hints.push(opts.missingHint);
    } else if (!opts.noMissing) {
      hints.push('未在 .env 中显式设置');
    }

    return (
      '<div class="field">' +
      '<label>' + esc(field.label) + ' <span class="mono dim">' + key + '</span></label>' +
      control +
      (hints.length ? '<span class="hint">' + hints.join(' · ') + '</span>' : '') +
      '</div>'
    );
  }

  // 配置分组「摘要卡」：只显示标题 / 说明 / 字段数，点击弹浮窗编辑
  // （渲染时自动登记到 _configItems，任何页面都能点击弹浮窗）
  function configBlock(item, isAdapter) {
    if (!_configItems[item.id || item.name]) {
      _configItems[item.id || item.name] = { item: item, isAdapter: !!isAdapter };
    }
    var disabled = item.available === false;
    var note = isAdapter
      ? (disabled ? badge('暂未支持', 'amber') + ' ' : badge('可用', 'green') + ' ') + esc(item.note || '')
      : esc(item.note || '');
    var keys = item.fields
      .map(function (f) {
        return '<span class="mono dim">' + esc(f.key) + '</span>';
      })
      .slice(0, 6)
      .join(' ');
    return (
      '<div class="card config-card config-mini" data-config-card="' + esc(item.id || item.name) + '">' +
      '<div class="card-head"><div>' +
      '<div class="card-title">' + esc(item.name) +
      (isAdapter ? ' ' + (disabled ? badge('暂未支持', 'amber') : badge('可用', 'green')) : '') +
      '</div>' +
      (note ? '<div class="card-note">' + note + '</div>' : '') +
      '</div>' +
      '<div class="config-mini-actions">' + badge(item.fields.length + ' 项', 'grey') +
      ' <span class="hint">点击编辑</span></div>' +
      '</div>' +
      '<div class="config-mini-keys">' + keys + (item.fields.length > 6 ? ' …' : '') + '</div>' +
      '</div>'
    );
  }

  // 打开某个配置分组的编辑浮窗
  function openConfigModal(item, isAdapter) {
    var note = isAdapter
      ? (item.available === false ? badge('暂未支持', 'amber') + ' ' : badge('可用', 'green') + ' ') + esc(item.note || '')
      : esc(item.note || '');
    // ⚠️ 「字段区 + 保存按钮 + 底部提示」必须整体包在 data-config-block 内，
    //    否则 bindConfigSaves 里 `btn.closest('[data-config-block]')` 拿到 null，
    //    collectConfig(null) 抛异常，保存请求根本发不出去（结构同 pluginDetailBody 的 data-plugin-config-block）。
    var body =
      (note ? '<div class="card-note" style="margin-bottom:12px">' + note + '</div>' : '') +
      '<div data-config-block data-config-id="' + esc(item.id || item.name) + '">' +
      '<div class="form-grid">' +
      item.fields.map(function (f) { return fieldInput(f, { noMissing: true }); }).join('') +
      '</div>' +
      '<div class="form-row spaced"><button class="btn" data-config-save>保存本节</button></div>' +
      '<div class="hint spaced">写入 <span class="mono">.config/main/.env</span>（自动备份 .env.bak），' +
      '<b>需重启 Bot 生效</b>（部分可热更项可点右上角「重载」立即生效）；敏感字段留空即不修改。</div>' +
      '</div>';
    var container = openModal(esc(item.name), body);
    bindChips(container);
    bindConfigSaves(container);
    bindSelectFilters(container);
  }

  function pageConfig() {
    return api('/config').then(function (d) {
      // 缓存分组数据，供点击弹浮窗使用
      _configItems = {};
      (d.adapters || []).forEach(function (a) {
        _configItems[a.id || a.name] = { item: a, isAdapter: true };
      });
      (d.sections || []).forEach(function (s) {
        _configItems[s.id || s.name] = { item: s, isAdapter: false };
      });

      var adapterCards = (d.adapters || [])
        .map(function (a) {
          return configBlock(a, true);
        })
        .join('');
      var sectionCards = (d.sections || [])
        .map(function (s) {
          return configBlock(s, false);
        })
        .join('');
      return (
        '<div class="error-box warn">' +
        '点击任意分组卡片可打开编辑浮窗；配置写入 <span class="mono">' + esc(d.path) + '</span>（自动备份 .env.bak），' +
        '<b>需重启 Bot 才会生效</b>；改完可点右上角「重载」让可热更项立即生效。敏感字段不回显，留空即不修改。' +
        '</div>' +
        '<div class="grid config-grid">' + adapterCards + sectionCards + '</div>'
      );
    });
  }

  var _configItems = {};

  function collectConfig(block) {
    var values = {};
    block.querySelectorAll('[data-key]').forEach(function (el) {
      var key = el.dataset.key;
      if (el.dataset.secret === '1') {
        var secret = el.value.trim();
        if (secret) values[key] = secret;
        return;
      }
      if (el.dataset.type === 'bool') {
        values[key] = el.value === 'true';
      } else if (el.dataset.type === 'int') {
        values[key] = el.value === '' ? null : parseInt(el.value, 10);
      } else if (el.dataset.type === 'float') {
        values[key] = el.value === '' ? null : parseFloat(el.value);
      } else if (el.dataset.type === 'list') {
        try {
          values[key] = JSON.parse(el.value);
        } catch (e) {
          values[key] = el.value;
        }
      } else {
        values[key] = el.value;
      }
    });
    return values;
  }

  function bindConfigSaves(root) {
    root.querySelectorAll('[data-config-save]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var block = btn.closest('[data-config-block]');
        btn.disabled = true;
        send('/config', 'PUT', { values: collectConfig(block) })
          .then(function (res) {
            toast(res.message || '已保存', 'ok');
            render();
          })
          .catch(function (err) {
            toast('保存失败：' + err.message, 'err');
          })
          .then(function () {
            btn.disabled = false;
          });
      });
    });
  }

  // chips（列表字段）交互：添加 / 删除 / 回车；改动同步隐藏输入
  function bindChips(scope) {
    scope.querySelectorAll('[data-chips]').forEach(function (box) {
      var key = box.dataset.chips;
      var hidden = scope.querySelector('input[type=hidden][data-key="' + key + '"]');
      var input = box.querySelector('[data-chip-input="' + key + '"]');
      var sync = function () {
        if (hidden) hidden.value = JSON.stringify(collectChips(box));
      };
      var add = function () {
        var v = (input.value || '').trim();
        if (!v) return;
        var dup = collectChips(box).indexOf(v) >= 0;
        if (dup) { toast('已在列表中', 'err'); return; }
        input.insertAdjacentHTML('beforebegin', chipHtml(v));
        input.value = '';
        sync();
      };
      var addBtn = box.querySelector('[data-chip-add="' + key + '"]');
      if (addBtn) addBtn.addEventListener('click', add);
      if (input) {
        input.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter') { ev.preventDefault(); add(); }
        });
      }
    });

    // 删除（事件委托，兼容后加的 chip）。
    // ⚠️ 去重（不叠加委托）：浮窗复用的是同一个 #modal-body，主页面复用的是同一个 #content，
    //    所以同一次会话里 bindChips 会被反复调用到同一个 scope 上。
    //    注意不能用「已绑定就 return」的朴素标记——#content 会被复用、内容被 innerHTML 换掉，
    //    一旦 return 就再也不会重新绑定（切页后 chips 就哑了）。这里改成：
    //    把上一次的委托函数记在 scope 上，挂新的之前先摘掉旧的，对复用元素与全新元素都安全。
    if (scope.__chipsDelegation) {
      scope.removeEventListener('click', scope.__chipsDelegation);
    }
    scope.__chipsDelegation = function (ev) {
      var t = ev.target;
      if (t && t.dataset && t.dataset.chipDel !== undefined) {
        var box = t.closest('[data-chips]');
        if (!box) return;
        t.closest('.chip').remove();
        var key = box.dataset.chips;
        var hidden = scope.querySelector('input[type=hidden][data-key="' + key + '"]');
        if (hidden) hidden.value = JSON.stringify(collectChips(box));
      }
    };
    scope.addEventListener('click', scope.__chipsDelegation);
  }

  // 长下拉的过滤输入：输入关键字，隐藏不匹配的 <option>
  // 支持两种写法：`[data-select-filter="<key>"]`（配 select[data-key]）
  //             `[data-filter-for="<selectId>"]`（配 select#id）
  function bindSelectFilter(scope, box, select) {
    if (!select || box.dataset.bound) return;
    box.dataset.bound = '1';
    box.addEventListener('input', function () {
      var kw = (box.value || '').trim().toLowerCase();
      var first = null;
      Array.prototype.forEach.call(select.options, function (opt) {
        var hit = kw === '' || String(opt.value).toLowerCase().indexOf(kw) >= 0;
        opt.hidden = !hit;
        if (hit && !first) first = opt;
      });
      if (first) select.value = first.value;
    });
  }

  function bindSelectFilters(scope) {
    scope.querySelectorAll('[data-select-filter]').forEach(function (box) {
      bindSelectFilter(scope, box, scope.querySelector('select[data-key="' + box.dataset.selectFilter + '"]'));
    });
  }

  function afterConfig(root) {
    // 点击分组摘要卡 → 弹浮窗编辑
    on(root, '[data-config-card]', 'click', function (card) {
      var entry = _configItems[card.dataset.configCard];
      if (!entry) return;
      openConfigModal(entry.item, entry.isAdapter);
    });
    bindChips(root);
    bindSelectFilters(root);
  }

  /* -------------------------------------------------------------- 黑白名单 */
  function grantRows(list, mode) {
    if (!list || !list.length) return empty('暂无记录');
    var head =
      mode === 'blacklist'
        ? '<tr><th>QQ</th><th>等级</th><th>操作人</th><th>时间</th><th>操作</th></tr>'
        : '<tr><th>QQ</th><th>作用域</th><th>等级</th><th>操作人</th><th>操作</th></tr>';
    var rows = list
      .map(function (g) {
        var scope =
          g.scope_type === 'global'
            ? '全局'
            : (SCOPE_LABEL[g.scope_type] || g.scope_type) + '[' + esc(g.scope_id) + ']';
        var del =
          '<button class="btn small danger" data-grant-del="' + esc(g.user_id) + '" ' +
          'data-grant-group="' + (g.scope_type === 'group' ? esc(g.scope_id) : '') + '">移除</button>';
        return (
          '<tr><td class="mono">' + esc(g.user_id) + '</td>' +
          (mode === 'blacklist' ? '' : '<td>' + scope + '</td>') +
          '<td>' + badge(g.level + ' ' + g.level_name, LEVEL_COLOR[g.level] || 'grey') + '</td>' +
          '<td>' + esc(g.updated_by || '-') + '</td>' +
          '<td>' + esc(g.updated_at || '-') + '</td>' +
          '<td>' + del + '</td></tr>'
        );
      })
      .join('');
    return '<div class="table-wrap"><table><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>';
  }

  // 群号候选 <datalist>（输入框 + 建议：可选已有群，也可手填）
  function groupDatalist(groups, id) {
    var opts = (groups || [])
      .map(function (g) {
        var label = (g.group_name ? g.group_name + ' ' : '') + g.group_id;
        return '<option value="' + esc(g.group_id) + '">' + esc(label) + '</option>';
      })
      .join('');
    return '<datalist id="' + esc(id) + '">' + opts + '</datalist>';
  }

  function pagePermissions() {
    return Promise.all([api('/permissions'), api('/groups')]).then(function (res) {
      var d = res[0];
      var groups = res[1];
      var groupListOptions = groupDatalist(groups.items, 'dl-groups-admin');

      var groupCards = groups.items.length
        ? groups.items
            .map(function (g) {
              var rules = g.rules.length
                ? g.rules
                    .map(function (r) {
                      return esc(r.plugin) + ' ' + (r.enabled ? badge('开', 'green') : badge('关', 'red'));
                    })
                    .join(' ')
                : '<span class="hint">无插件规则</span>';
              var grants = g.grants.length
                ? g.grants
                    .map(function (x) {
                      return esc(x.user_id) + '（' + esc(x.level_name) + '）';
                    })
                    .join('、')
                : '<span class="hint">无手动授权</span>';
              return (
                '<div class="card" data-group-card="' + esc(g.group_id) + '">' +
                '<div class="card-head"><div>' +
                '<div class="card-title">' + esc(g.group_name || '（未知群名）') +
                ' <span class="mono dim">' + esc(g.group_id) + '</span></div>' +
                '<div class="card-note">' +
                (g.online
                  ? badge('在线', 'green') + ' ' + esc((g.member_count || '?') + ' 人')
                  : badge('仅数据库记录', 'grey')) +
                ' · 规则 ' + g.rules.length + ' 条 · 授权 ' + g.grants.length + ' 条</div>' +
                '</div><button class="btn small ghost" data-group-members="' + esc(g.group_id) + '">查看成员</button></div>' +
                '<div class="card-body">' + kv([['插件规则', rules], ['手动授权', grants]]) + '</div>' +
                '<div class="member-slot"></div>' +
                '</div>'
              );
            })
            .join('')
        : empty('还没有任何群（Bot 未在线，数据库里也没有群记录）');

      return (
        '<div class="two-col">' +
        card(
          '全局黑名单（0 级）',
          grantRows(d.blacklist, 'blacklist') +
            '<div class="form-row spaced">' +
            '<div class="field"><label>QQ 号</label><input class="input" data-blacklist-input placeholder="如 123456"></div>' +
            '<button class="btn danger" data-blacklist-add>加入黑名单</button>' +
            '</div>',
          '黑名单一票否决：不回应任何消息'
        ) +
        card(
          '分群管理员（2 级）',
          grantRows(d.group_admins, 'group') +
            '<div class="form-row spaced">' +
            '<div class="field"><label>QQ 号</label><input class="input short" data-admin-uid placeholder="QQ"></div>' +
            '<div class="field"><label>群号</label><input class="input short" list="dl-groups-admin" data-admin-gid placeholder="选择或输入群号"></div>' +
            groupListOptions +
            '<button class="btn" data-admin-add>授权管理员</button>' +
            '</div>',
          '仅在该群生效'
        ) +
        '</div>' +
        (d.others.length ? card('其它授权记录', grantRows(d.others, 'group')) : '') +
        card(
          '群列表与成员权限',
          groupCards,
          '共 ' + groups.count + ' 个群 · ' + (groups.online ? '数据来自协议端' : '协议端离线，仅显示数据库记录')
        )
      );
    });
  }

  function loadMembers(groupId, cardEl) {
    var slot = cardEl.querySelector('.member-slot');
    slot.innerHTML = '<div class="loading">加载成员…</div>';
    return api('/groups/' + encodeURIComponent(groupId) + '/members')
      .then(function (d) {
        var rows = d.items
          .map(function (m) {
            var manual = m.manual ? badge('手动 ' + m.manual.level_name, 'blue') : '<span class="hint">-</span>';
            var banned = m.globally_banned ? badge('全局黑名单', 'red') : '';
            return (
              '<tr><td class="mono">' + esc(m.user_id) + '</td>' +
              '<td>' + esc(m.card || m.nickname || '-') + '</td>' +
              '<td>' + badge(ROLE_LABEL[m.role] || m.role, m.role === 'member' ? 'grey' : 'blue') + '</td>' +
              '<td>' + badge(m.level + ' ' + m.level_name, LEVEL_COLOR[m.level] || 'grey') + ' ' + banned + '</td>' +
              '<td>' + manual + '</td>' +
              '<td><button class="btn small plain" data-member-admin="' + esc(m.user_id) + '" data-gid="' + esc(groupId) + '">设为管理员</button>' +
              '<button class="btn small danger" data-member-black="' + esc(m.user_id) + '">拉黑</button></td></tr>'
            );
          })
          .join('');
        slot.innerHTML =
          '<div class="member-panel">' +
          '<div class="card-note" style="margin-bottom:8px">共 ' + d.count + ' 名成员（按群角色排序）</div>' +
          '<div class="table-wrap"><table><thead><tr>' +
          '<th>QQ</th><th>昵称</th><th>群角色</th><th>有效权限</th><th>手动授权</th><th>操作</th>' +
          '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
        bindMemberActions(slot);
      })
      .catch(function (err) {
        slot.innerHTML = errorBox(err);
      });
  }

  function bindMemberActions(root) {
    root.querySelectorAll('[data-member-admin]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        send('/permissions', 'PUT', {
          user_id: btn.dataset.memberAdmin,
          level: 2,
          group_id: btn.dataset.gid,
        })
          .then(function () {
            toast('已设为该群管理员', 'ok');
            return loadMembers(btn.dataset.gid, btn.closest('[data-group-card]'));
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
          });
      });
    });
    root.querySelectorAll('[data-member-black]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!confirm('确认把 ' + btn.dataset.memberBlack + ' 加入全局黑名单？')) return;
        send('/permissions', 'PUT', { user_id: btn.dataset.memberBlack, level: 0 })
          .then(function () {
            toast('已拉黑', 'ok');
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
          });
      });
    });
  }

  function afterPermissions(root) {
    root.querySelectorAll('[data-grant-del]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var uid = btn.dataset.grantDel;
        var gid = btn.dataset.grantGroup;
        if (!confirm('确认移除 ' + uid + ' 的授权' + (gid ? '（群 ' + gid + '）' : '（全局）') + '？')) return;
        send('/permissions?user_id=' + encodeURIComponent(uid) + '&group_id=' + encodeURIComponent(gid || ''), 'DELETE')
          .then(function () {
            toast('已移除', 'ok');
            render();
          })
          .catch(function (err) {
            toast('移除失败：' + err.message, 'err');
          });
      });
    });

    var blackAdd = root.querySelector('[data-blacklist-add]');
    if (blackAdd) {
      blackAdd.addEventListener('click', function () {
        var uid = root.querySelector('[data-blacklist-input]').value.trim();
        if (!uid) return toast('请填写 QQ 号', 'err');
        send('/permissions', 'PUT', { user_id: uid, level: 0 })
          .then(function () {
            toast('已拉黑 ' + uid, 'ok');
            render();
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
          });
      });
    }

    var adminAdd = root.querySelector('[data-admin-add]');
    if (adminAdd) {
      adminAdd.addEventListener('click', function () {
        var uid = root.querySelector('[data-admin-uid]').value.trim();
        var gid = root.querySelector('[data-admin-gid]').value.trim();
        if (!uid || !gid) return toast('QQ 号和群号都要填', 'err');
        send('/permissions', 'PUT', { user_id: uid, level: 2, group_id: gid })
          .then(function () {
            toast('已授权', 'ok');
            render();
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
          });
      });
    }

    root.querySelectorAll('[data-group-members]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var cardEl = btn.closest('[data-group-card]');
        if (cardEl.dataset.loaded === '1') {
          cardEl.querySelector('.member-slot').innerHTML = '';
          cardEl.dataset.loaded = '0';
          btn.textContent = '查看成员';
          return;
        }
        btn.textContent = '收起成员';
        cardEl.dataset.loaded = '1';
        loadMembers(btn.dataset.groupMembers, cardEl);
      });
    });
  }

  /* -------------------------------------------------------------- 插件管理 */
  function scopeText(rule) {
    return rule.scope_type === 'global'
      ? '全局'
      : (SCOPE_LABEL[rule.scope_type] || rule.scope_type) + ' ' + esc(rule.scope_id);
  }

  function ruleRow(name, rule, locked) {
    var del = locked
      ? badge('受保护', 'grey')
      : '<button class="btn small danger" data-rule-del="' + esc(name) + '" ' +
        'data-scope-type="' + esc(rule.scope_type) + '" data-scope-id="' + esc(rule.scope_id) + '">删除</button>';
    return (
      '<tr><td>' + scopeText(rule) + '</td>' +
      '<td>' + (rule.enabled ? badge('允许运行', 'green') : badge('禁止运行', 'red')) + '</td>' +
      '<td>' + esc(rule.updated_by || '-') + '</td>' +
      '<td>' + esc(rule.updated_at || '-') + '</td>' +
      '<td>' + del + '</td></tr>'
    );
  }

  var _pluginDetails = {};
  var _pluginGroups = [];

  // 渲染某个插件的「详情浮窗」正文（门控规则 + 注册命令 + 配置项 + 写入规则表单）
  function pluginDetailBody(p) {
    var locked = p.protected;
    var groupOptions = _pluginGroups
      .map(function (g) {
        return '<option value="' + esc(g.group_id) + '">' + esc(g.group_name || g.group_id) + '</option>';
      })
      .join('');

    var rules = p.rules.length
      ? '<div class="table-wrap"><table><thead><tr>' +
        '<th>作用域</th><th>状态</th><th>修改者</th><th>时间</th><th>操作</th>' +
        '</tr></thead><tbody>' +
        p.rules.map(function (r) { return ruleRow(p.name, r, locked); }).join('') +
        '</tbody></table></div>'
      : empty('暂无显式规则（按默认值运行）');

    var commands = p.commands.length
      ? '<div class="table-wrap"><table><thead><tr><th>命令</th><th>说明</th></tr></thead><tbody>' +
        p.commands
          .map(function (c) {
            var aliases = c.aliases && c.aliases.length ? '（别名 ' + c.aliases.join('、') + '）' : '';
            return (
              '<tr><td class="mono">' + esc(c.commands.join(' / ')) + esc(aliases) + '</td>' +
              '<td>' + esc(c.description || '-') + '</td></tr>'
            );
          })
          .join('') +
        '</tbody></table></div>'
      : empty('该插件未注册命令');

    var configFields = p.config && p.config.fields ? p.config.fields : [];
    var configBlock = !configFields.length
      ? ''
      : '<div data-plugin-config-block="' + esc(p.name) + '">' +
        '<div class="form-grid">' +
        configFields
          .map(function (f) {
            return fieldInput(f, { noMissing: true, missingHint: '使用默认值', configuredHint: '已自定义' });
          })
          .join('') +
        '</div>' +
        '<div class="form-row spaced">' +
        '<button class="btn" data-plugin-config-save="' + esc(p.name) + '">保存配置</button>' +
        '<button class="btn ghost" data-plugin-config-clear="' + esc(p.name) + '">恢复默认</button>' +
        '</div></div>';

    var addForm = locked
      ? ''
      : '<div class="form-row spaced">' +
        '<div class="field"><label>作用域</label><select class="input" data-rule-scope="' + esc(p.name) + '">' +
        '<option value="global">全局</option><option value="group">指定群</option>' +
        '<option value="member">指定成员</option></select></div>' +
        '<div class="field" data-rule-id-wrap="' + esc(p.name) + '" style="display:none">' +
        '<label>群号 / QQ</label><input class="input" list="group-list-' + esc(p.name) + '" ' +
        'data-rule-id="' + esc(p.name) + '" placeholder="群号或 QQ"></div>' +
        '<datalist id="group-list-' + esc(p.name) + '">' + groupOptions + '</datalist>' +
        '<div class="field"><label>状态</label><select class="input" data-rule-enabled="' + esc(p.name) + '">' +
        '<option value="true">允许运行</option><option value="false">禁止运行</option></select></div>' +
        '<button class="btn" data-rule-add="' + esc(p.name) + '">写入规则</button>' +
        '</div>';

    var toggle =
      '<label class="switch" title="' + (locked ? '受保护插件不可关闭' : '全局开关') + '">' +
      '<input type="checkbox" data-global-toggle="' + esc(p.name) + '"' +
      (p.global_enabled ? ' checked' : '') + (locked ? ' disabled' : '') +
      '><span class="slider"></span></label>';

    return (
      '<div class="detail-head-row">' +
      '<div class="plugin-detail-note">' + esc(p.description || '暂无描述') +
      (p.author ? ' · 作者 ' + esc(p.author) : '') +
      (p.repo ? ' · <a href="' + esc(p.repo) + '" target="_blank" rel="noreferrer">仓库</a>' : '') +
      (p.requires ? ' · 要求 <span class="mono">' + esc(p.requires) + '</span>' : '') +
      (locked ? ' ' + badge('受保护', 'pink') : '') +
      '</div>' +
      '<div class="detail-toggle"><span class="detail-toggle-label">' + (p.global_enabled ? '运行中' : '已停用') + '</span>' + toggle + '</div>' +
      '</div>' +
      '<div class="detail-section"><div class="detail-title">门控规则</div>' + rules + addForm + '</div>' +
      '<div class="detail-section"><div class="detail-title">注册命令</div>' + commands + '</div>' +
      (configFields.length ? '<div class="detail-section"><div class="detail-title">插件配置项</div>' + configBlock + '</div>' : '')
    );
  }

  // 通用浮窗：openModal(标题HTML, 正文HTML) / closeModal()（全站共用同一套骨架与遮罩）
  function openModal(titleHtml, bodyHtml) {
    document.getElementById('modal-title').innerHTML = titleHtml || '';
    document.getElementById('modal-body').innerHTML = bodyHtml || '';
    document.getElementById('modal-overlay').style.display = 'flex';
    return document.getElementById('modal-body');
  }

  function closeModal() {
    var overlay = document.getElementById('modal-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  // 打开插件详情浮窗
  function openPluginModal(name) {
    var p = _pluginDetails[name];
    if (!p) return;
    var body = openModal(
      esc(p.display_name || p.name) + ' <span class="mono dim">' + esc(p.name) + '</span> ' +
        badge('v' + p.version, 'grey') + (p.protected ? ' ' + badge('受保护', 'pink') : ''),
      pluginDetailBody(p)
    );
    bindPluginActions(body);
  }

  function closePluginModal() {
    closeModal();
  }

  function pagePlugins() {
    return Promise.all([api('/plugins'), api('/groups')]).then(function (res) {
      var list = res[0];
      var groups = res[1];
      // 列表数据带 global_enabled，详情若缺该字段就回落（后端已返回，纯前端加固）
      var listById = {};
      list.items.forEach(function (item) {
        listById[item.name] = item;
      });
      return Promise.all(
        list.items.map(function (p) {
          return api('/plugins/' + encodeURIComponent(p.name)).then(function (detail) {
            if (detail.global_enabled === undefined && listById[detail.name]) {
              detail.global_enabled = listById[detail.name].global_enabled;
            }
            if (!detail.config_schema || !detail.config_schema.length) return detail;
            return api('/plugins/' + encodeURIComponent(p.name) + '/config').then(function (cfg) {
              detail.config = cfg;
              return detail;
            });
          });
        })
      ).then(function (details) {
        _pluginGroups = groups.items;
        details.forEach(function (p) { _pluginDetails[p.name] = p; });

        var cards = details
          .map(function (p) {
            var locked = p.protected;
            return (
              '<div class="card plugin-card" data-plugin-card="' + esc(p.name) + '">' +
              '<div class="card-head"><div>' +
              '<div class="card-title">' + esc(p.display_name || p.name) +
              ' <span class="mono dim">' + esc(p.name) + '</span>' +
              '</div>' +
              '<div class="card-note">' + esc(p.description || '暂无描述') + '</div>' +
              '</div>' +
              '</div>' +
              '<div class="plugin-card-foot">' +
              badge('v' + p.version, 'grey') + ' ' +
              (p.global_enabled ? badge('运行中', 'green') : badge('已停用', 'red')) +
              (locked ? ' ' + badge('受保护', 'pink') : '') +
              '</div>' +
              '</div>'
            );
          })
          .join('');

        return (
          '<div class="error-box warn">点击插件卡片可打开详情浮窗（门控规则 / 命令 / 配置项）。' +
          '改插件代码后可用右上角「重载」；受保护插件不允许通过 WebUI 关闭。</div>' +
          (cards ? '<div class="plugin-grid">' + cards + '</div>' : empty('没有加载任何插件'))
        );
      });
    });
  }

  // 绑定插件详情操作（主页面卡片 + 浮窗正文共用）
  function bindPluginActions(scope) {
    scope.querySelectorAll('[data-global-toggle]').forEach(function (box) {
      box.addEventListener('change', function () {
        var name = box.dataset.globalToggle;
        box.disabled = true;
        send('/plugins/' + encodeURIComponent(name) + '/rule', 'PUT', {
          scope_type: 'global',
          scope_id: '',
          enabled: box.checked,
        })
          .then(function () {
            toast(name + ' 已' + (box.checked ? '启用' : '停用'), 'ok');
            closePluginModal();
            render();
          })
          .catch(function (err) {
            box.checked = !box.checked;
            toast('操作失败：' + err.message, 'err');
          })
          .then(function () {
            box.disabled = false;
          });
      });
    });

    scope.querySelectorAll('[data-rule-scope]').forEach(function (select) {
      select.addEventListener('change', function () {
        var name = select.dataset.ruleScope;
        var wrap = scope.querySelector('[data-rule-id-wrap="' + name + '"]');
        wrap.style.display = select.value === 'global' ? 'none' : '';
      });
    });

    scope.querySelectorAll('[data-rule-add]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.ruleAdd;
        var scopeVal = scope.querySelector('[data-rule-scope="' + name + '"]').value;
        var scopeId = scopeVal === 'global' ? '' : scope.querySelector('[data-rule-id="' + name + '"]').value.trim();
        var enabled = scope.querySelector('[data-rule-enabled="' + name + '"]').value === 'true';
        if (scopeVal !== 'global' && !scopeId) return toast('请填写群号 / QQ', 'err');
        send('/plugins/' + encodeURIComponent(name) + '/rule', 'PUT', {
          scope_type: scopeVal,
          scope_id: scopeId,
          enabled: enabled,
        })
          .then(function () {
            toast('规则已写入', 'ok');
            closePluginModal();
            render();
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
          });
      });
    });

    scope.querySelectorAll('[data-rule-del]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.ruleDel;
        var query =
          '?scope_type=' + encodeURIComponent(btn.dataset.scopeType) +
          '&scope_id=' + encodeURIComponent(btn.dataset.scopeId);
        send('/plugins/' + encodeURIComponent(name) + '/rule' + query, 'DELETE')
          .then(function () {
            toast('规则已删除', 'ok');
            closePluginModal();
            render();
          })
          .catch(function (err) {
            toast('删除失败：' + err.message, 'err');
          });
      });
    });

    // 插件配置项：保存
    scope.querySelectorAll('[data-plugin-config-save]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.pluginConfigSave;
        var block = scope.querySelector('[data-plugin-config-block="' + name + '"]');
        btn.disabled = true;
        send('/plugins/' + encodeURIComponent(name) + '/config', 'PUT', {
          values: collectConfig(block),
        })
          .then(function (res) {
            var changed = res.changed || [];
            toast(changed.length ? '已保存：' + changed.join('、') : '没有需要修改的配置项', 'ok');
            closePluginModal();
            render();
          })
          .catch(function (err) {
            toast('保存失败：' + err.message, 'err');
          })
          .then(function () {
            btn.disabled = false;
          });
      });
    });

    // 插件配置项：一键恢复默认
    scope.querySelectorAll('[data-plugin-config-clear]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.pluginConfigClear;
        btn.disabled = true;
        send('/plugins/' + encodeURIComponent(name) + '/config', 'DELETE')
          .then(function (res) {
            toast('已恢复默认（清除 ' + res.removed + ' 项）', 'ok');
            closePluginModal();
            render();
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
          })
          .then(function () {
            btn.disabled = false;
          });
      });
    });
  }

  function afterPlugins(root) {
    bindPluginActions(root);

    // 点击插件卡片 → 打开详情浮窗
    on(root, '[data-plugin-card]', 'click', function (card) {
      openPluginModal(card.dataset.pluginCard);
    });
  }

  /* ------------------------------------------------------------ 数据库管理 */
  function pageDatabase() {
    return api('/database').then(function (d) {
      var tables = d.tables.length
        ? '<div class="table-wrap"><table><thead><tr>' +
          '<th>表名</th><th>说明</th><th>行数</th><th>列</th><th>操作</th>' +
          '</tr></thead><tbody>' +
          d.tables
            .map(function (t) {
              return (
                '<tr><td class="mono">' + esc(t.name) + '</td><td>' + esc(t.label || '-') + '</td>' +
                '<td>' + t.rows + '</td><td class="hint">' + esc(t.columns.join(', ')) + '</td>' +
                '<td><button class="btn small plain" data-table-open="' + esc(t.name) + '">查看数据</button></td></tr>'
              );
            })
            .join('') +
          '</tbody></table></div>'
        : empty('数据库里还没有表（启动后会自动建表）');

      return (
        '<div class="two-col">' +
        card(
          '数据库文件',
          kv([
            ['路径', '<span class="mono">' + esc(d.file.path) + '</span>'],
            ['大小', esc(d.file.size_text) + (d.file.exists ? '' : '（文件尚未创建）')],
            ['修改时间', esc(d.file.modified_at || '-')],
            ['journal 模式', esc(d.engine.journal_mode)],
            ['SQLite 版本', esc(d.engine.version)],
          ]),
          '只读浏览：不提供任意 SQL 执行'
        ) +
        card('表清单', tables) +
        '</div>' +
        '<div id="table-view"></div>'
      );
    });
  }

  function loadTable(name, offset) {
    var limit = state.db.limit;
    var view = document.getElementById('table-view');
    view.innerHTML = '<div class="loading">读取 ' + esc(name) + ' …</div>';
    return api('/database/tables/' + encodeURIComponent(name) + '?limit=' + limit + '&offset=' + (offset || 0)).then(
      function (d) {
        state.db.name = name;
        state.db.offset = d.offset;
        state.db.total = d.total;
        var head = d.columns.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('');
        var body = d.rows.length
          ? d.rows
              .map(function (row) {
                return '<tr>' + row.map(function (cell) { return '<td class="mono">' + esc(cell) + '</td>'; }).join('') + '</tr>';
              })
              .join('')
          : '<tr><td colspan="' + d.columns.length + '" class="hint">该表暂无数据</td></tr>';

        var from = d.total ? d.offset + 1 : 0;
        var to = Math.min(d.offset + d.limit, d.total);
        var prev = Math.max(0, d.offset - d.limit);
        var next = d.offset + d.limit;

        view.innerHTML =
          '<div class="card"><div class="card-head"><div>' +
          '<div class="card-title">' + esc(d.name) + ' <span class="mono dim">' + esc(d.label || '') + '</span></div>' +
          '<div class="card-note">共 ' + d.total + ' 行 · 显示第 ' + from + ' - ' + to + ' 行</div>' +
          '</div><div class="top-actions">' +
          '<button class="btn small ghost" data-table-page="' + prev + '"' + (d.offset <= 0 ? ' disabled' : '') + '>上一页</button>' +
          '<button class="btn small ghost" data-table-page="' + next + '"' + (to >= d.total ? ' disabled' : '') + '>下一页</button>' +
          '<button class="btn small ghost" data-table-close>收起</button>' +
          '</div></div>' +
          '<div class="table-wrap"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>' +
          '</div>';

        view.querySelectorAll('[data-table-page]').forEach(function (btn) {
          btn.addEventListener('click', function () {
            loadTable(d.name, parseInt(btn.dataset.tablePage, 10) || 0);
          });
        });
        var closeBtn = view.querySelector('[data-table-close]');
        if (closeBtn) {
          closeBtn.addEventListener('click', function () {
            view.innerHTML = '';
          });
        }
      }
    );
  }

  function afterDatabase(root) {
    root.querySelectorAll('[data-table-open]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        loadTable(btn.dataset.tableOpen, 0);
      });
    });
  }

  /* -------------------------------------------------------------- 命令管理 */
  function levelSelect(c, levels) {
    if (!c.editable) {
      return (
        badge(c.level_label, c.level_color) +
        ' <span class="hint">' + (c.permission === 'custom' ? '插件自管' : '受保护') + '</span>'
      );
    }
    var opts = levels
      .map(function (lv) {
        return (
          '<option value="' + lv.value + '"' + (lv.value === c.level ? ' selected' : '') + '>' +
          esc(lv.label) + '（' + lv.value + ' 级）</option>'
        );
      })
      .join('');
    return (
      '<select class="input" data-cmd-level data-plugin="' + esc(c.plugin) +
      '" data-command="' + esc(c.name) + '" style="min-width:130px">' + opts +
      '</select>' +
      (c.overridden
        ? ' <button class="btn ghost small" data-cmd-reset data-plugin="' + esc(c.plugin) +
          '" data-command="' + esc(c.name) + '" title="恢复默认：' +
          esc(c.default_level_label) + '">恢复默认</button>'
        : '')
    );
  }

  // 命令数据缓存：单张大表在前端做筛选/排序/搜索，无需重新请求
  var _cmdData = { rows: [], levels: [], prefix: '' };

  function commandsRowHtml(c) {
    var levelCell = levelSelect(c, _cmdData.levels);
    var tip = c.overridden ? '已改过（默认 ' + esc(c.default_level_label) + '）' : '默认';
    return (
      '<tr data-cmd-plugin="' + esc(c.plugin) + '" data-cmd-level-val="' + c.level + '">' +
      '<td class="mono">' + esc(c.display) + '</td>' +
      '<td class="mono hint">' + esc(c.alias_display || '-') + '</td>' +
      '<td>' + esc(c.description || '-') + '</td>' +
      '<td class="mono dim">' + esc(c.plugin) + '</td>' +
      '<td>' + levelCell + '<div class="hint">' + tip + '</div></td>' +
      '</tr>'
    );
  }

  function renderCommandsTable(root) {
    var plugin = (root.querySelector('#cmd-filter-plugin') || {}).value || '';
    var level = (root.querySelector('#cmd-filter-level') || {}).value || '';
    var kw = ((root.querySelector('#cmd-search') || {}).value || '').trim().toLowerCase();
    var sort = (root.querySelector('#cmd-sort') || {}).value || 'plugin';

    var rows = _cmdData.rows.filter(function (c) {
      if (plugin && c.plugin !== plugin) return false;
      if (level && String(c.level) !== level) return false;
      if (kw) {
        var hay = (c.display + ' ' + (c.alias_display || '') + ' ' + (c.description || '') + ' ' + c.plugin).toLowerCase();
        if (hay.indexOf(kw) < 0) return false;
      }
      return true;
    });
    rows.sort(function (a, b) {
      if (sort === 'level') return a.level - b.level || a.plugin.localeCompare(b.plugin);
      if (sort === 'name') return a.display.localeCompare(b.display);
      return a.plugin.localeCompare(b.plugin) || a.display.localeCompare(b.display);
    });

    var body = root.querySelector('#cmd-tbody');
    if (!body) return;
    body.innerHTML = rows.length
      ? rows.map(commandsRowHtml).join('')
      : '<tr><td colspan="5" class="hint">没有符合条件的命令</td></tr>';
    var cnt = root.querySelector('#cmd-count');
    if (cnt) cnt.textContent = '显示 ' + rows.length + ' / ' + _cmdData.rows.length + ' 条';
    bindCommandRows(root);
  }

  function bindCommandRows(root) {
    on(root.querySelector('#cmd-tbody') || root, '[data-cmd-level]', 'change', function (sel) {
      mutate(
        '/commands/' + encodeURIComponent(sel.dataset.plugin) + '/' + encodeURIComponent(sel.dataset.command),
        'PUT',
        { level: parseInt(sel.value, 10) },
        { ok: '已更新并即时生效', err: '更新失败', btn: sel }
      );
    });
    on(root.querySelector('#cmd-tbody') || root, '[data-cmd-reset]', 'click', function (btn) {
      mutate(
        '/commands/' + encodeURIComponent(btn.dataset.plugin) + '/' + encodeURIComponent(btn.dataset.command),
        'DELETE',
        undefined,
        { ok: '已恢复默认', err: '操作失败', btn: btn }
      );
    });
  }

  function pageCommands() {
    return api('/commands').then(function (d) {
      var levels = d.levels || [];
      _cmdData.levels = levels;
      _cmdData.prefix = d.prefix || '';
      _cmdData.rows = [];
      (d.items || []).forEach(function (p) {
        (p.commands || []).forEach(function (c) {
          c.plugin = p.plugin;
          _cmdData.rows.push(c);
        });
      });

      var pluginOpts = ['<option value="">全部插件</option>']
        .concat(
          (d.items || []).map(function (p) {
            return '<option value="' + esc(p.plugin) + '">' + esc(p.display_name || p.plugin) + '</option>';
          })
        )
        .join('');
      var levelOpts = ['<option value="">全部权限</option>']
        .concat(levels.map(function (lv) {
          return '<option value="' + lv.value + '">' + esc(lv.label) + '</option>';
        }))
        .join('');

      return (
        '<div class="error-box warn">群里发命令要带前缀 <span class="mono">' + esc(d.prefix || '（未设置）') +
        '</span>（如 <span class="mono">' + esc(d.prefix) + 'help</span>）。共 ' + d.plugin_count +
        ' 个插件 / ' + d.command_count + ' 条命令，当前 ' + (d.override_count || 0) +
        ' 条被改过。实际权限由框架维护，<b>改完即时生效</b>；受保护 / 插件自管的命令不可改。</div>' +
        card(
          '命令总览',
          '<div class="cmd-toolbar">' +
            '<input class="input" id="cmd-search" placeholder="搜索命令 / 别名 / 介绍…">' +
            '<select class="input" id="cmd-filter-plugin">' + pluginOpts + '</select>' +
            '<select class="input" id="cmd-filter-level">' + levelOpts + '</select>' +
            '<select class="input" id="cmd-sort">' +
              '<option value="plugin">按来源插件</option>' +
              '<option value="level">按所需权限</option>' +
              '<option value="name">按命令名</option>' +
            '</select>' +
            '<span class="hint" id="cmd-count"></span>' +
          '</div>' +
          '<div class="table-wrap"><table><thead><tr>' +
          '<th>命令</th><th>别名</th><th>功能介绍</th><th>来源插件</th><th>所需权限</th>' +
          '</tr></thead><tbody id="cmd-tbody"></tbody></table></div>',
          '点筛选/排序即时在本地过滤，不重新请求'
        )
      );
    });
  }

  function afterCommands(root) {
    renderCommandsTable(root);
    ['#cmd-search', '#cmd-filter-plugin', '#cmd-filter-level', '#cmd-sort'].forEach(function (sel) {
      var el = root.querySelector(sel);
      if (el) el.addEventListener('input', function () { renderCommandsTable(root); });
      if (el) el.addEventListener('change', function () { renderCommandsTable(root); });
    });
  }

  /* ---------------------------------------------------------------- 设置页 */

  /* 版本控制卡片（独立请求，取不到也不影响设置页其它内容） */
  function versionCardHtml(vc) {
    if (!vc || !vc.enabled) {
      return card(
        '版本控制',
        empty('版本控制功能已关闭（VCS_ENABLE=false）'),
        '🔄 一键拉取云端版本'
      );
    }

    var svc = vc.service || {};
    var svcBadge =
      svc.state === 'running'
        ? badge('运行中', 'green')
        : svc.state === 'none'
        ? badge('未配置', 'grey')
        : badge(svc.state || '未知', 'amber');

    if (!vc.available) {
      return card(
        '版本控制',
        kv([
          ['状态', badge(vc.reason || '不可用', 'red')],
          ['Git', vc.git_version ? esc(vc.git_version) : badge('未安装', 'red')],
          ['守护服务', svcBadge + ' <span class="hint">' + esc(svc.detail || '') + '</span>'],
        ]),
        '🔄 一键拉取云端版本'
      );
    }

    var dirtyState = vc.dirty
      ? badge('有 ' + vc.dirty_files.length + ' 处未提交改动', 'red')
      : badge('干净', 'green');
    var syncState = vc.compare_unknown
      ? badge('未知（尚未拉取）', 'grey')
      : vc.behind > 0
      ? badge('落后 ' + vc.behind + ' 个提交', 'amber')
      : vc.ahead > 0
      ? badge('领先 ' + vc.ahead + ' 个提交', 'blue')
      : badge('已是最新', 'green');

    var dirtyWarn = vc.dirty
      ? '<div class="error-box warn">工作区有未提交改动，已禁用更新（生产环境请勿手改代码，' +
        '请在本地改好并 push 后再更新）。文件：<span class="mono">' +
        esc(vc.dirty_files.slice(0, 5).join('、')) +
        (vc.dirty_files.length > 5 ? ' 等' : '') +
        '</span></div>'
      : '';

    var body =
      kv([
        ['当前版本', '<span class="mono">' + esc(vc.commit || '-') + '</span> ' +
          badge('分支 ' + esc(vc.branch), 'grey')],
        ['工作区', dirtyState],
        ['与远程', syncState],
        ['远程', '<span class="mono">' + esc(vc.remote_url || vc.remote) + '</span>'],
        ['守护服务', svcBadge + ' <span class="hint">' + esc(svc.detail || '') + '</span>'],
        ['Git', esc(vc.git_version || '未知')],
      ]) +
      dirtyWarn +
      '<div class="form-row spaced">' +
        '<button class="btn ghost" id="vc-check">检查更新</button>' +
        '<button class="btn" id="vc-update"' + (vc.dirty ? ' disabled' : '') + '>更新到最新</button>' +
        '<button class="btn danger" id="vc-restart">重启 Bot</button>' +
      '</div>' +
      '<div id="vc-commits" class="spaced"></div>' +
      '<div class="hint spaced">更新后需<b>重启</b>才生效（框架代码无法热重载）。' +
      '当前重启模式：<span class="mono">' + esc(vc.restart_mode) + '</span>' +
      '（spawn = 自己拉起；exit = 交给守护进程）。</div>';

    return card('版本控制', body, '🔄 一键拉取云端版本（只拉不推）');
  }

  function renderVersionCommits(root, data) {
    var box = root.querySelector('#vc-commits');
    if (!box) return;
    var items = data.commits || [];
    if (!items.length) {
      box.innerHTML = empty('没有提交记录');
      return;
    }
    var rows = items
      .map(function (c) {
        return (
          '<tr><td><input type="radio" name="vc-commit" value="' + esc(c.hash) + '"></td>' +
          '<td class="mono">' + esc(c.short) + '</td>' +
          '<td class="hint">' + esc((c.date || '').replace('T', ' ').slice(0, 19)) + '</td>' +
          '<td class="hint">' + esc(c.author) + '</td>' +
          '<td>' + esc(c.subject) + '</td></tr>'
        );
      })
      .join('');
    box.innerHTML =
      '<div class="card"><div class="card-head"><div>' +
      '<div class="card-title">提交历史 · ' + esc(data.message || '') + '</div>' +
      '<div class="card-note">选中一条提交后点右侧按钮即可切换（可用于回滚）</div>' +
      '</div><button class="btn small" id="vc-switch">切换到此版本</button></div>' +
      '<div class="table-wrap"><table><thead><tr>' +
      '<th></th><th>commit</th><th>时间</th><th>作者</th><th>说明</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
  }

  function pageSettings() {
    return Promise.all([
      api('/settings'),
      api('/version').catch(function () { return null; }),
    ]).then(function (res) {
      var d = res[0];
      var vc = res[1];
      var v = d.version;
      var r = d.runtime;
      var sec = d.security;

      var versionCard = card(
        '版本信息',
        kv([
          ['PaperBot', badge('v' + v.paperbot, 'pink')],
          ['NoneBot', esc(v.nonebot || '未知')],
          ['OneBot 适配器', esc(v.adapter_onebot || '未安装')],
          ['Python', esc(v.python)],
          ['项目目录', '<span class="mono">' + esc(v.project_dir) + '</span>'],
        ]),
        r.plugins + ' 个插件 · ' + r.commands + ' 条命令'
      );

      var runtimeCard = card(
        '运行时',
        kv([
          ['启动时间', esc(r.started_at)],
          ['已运行', esc(r.uptime_text)],
          ['监听地址', '<span class="mono">' + esc(r.bind) + '</span>'],
          ['API 前缀', '<span class="mono">' + esc(r.prefix) + '</span>'],
          ['运行环境', esc(r.environment)],
        ])
      );

      var tokenState = sec.token.configured
        ? badge('已设置', 'green') + ' <span class="dim">长度 ' + sec.token.length + '</span>'
        : badge('未设置（不鉴权）', 'amber');

      var tokenCard = card(
        'WebUI 访问令牌',
        '<div class="form-row">' +
          '<div class="field"><label>当前状态</label><div>' + tokenState + '</div></div>' +
          '<div class="field" style="flex:1;min-width:260px"><label>新令牌</label>' +
          '<input class="input wide" id="new-token" type="password" placeholder="至少 ' +
          sec.min_length + ' 位；留空 = 关闭鉴权" autocomplete="new-password" /></div>' +
          '<button class="btn" id="save-token">更新令牌</button>' +
        '</div>' +
        '<div class="hint spaced">写入 <span class="mono">' + esc(sec.env_file) +
        '</span>（自动备份 .env.bak），<b>立即生效</b>，无需重启；更新后会自动填到右上角。</div>',
        '🔒 公网部署务必设置'
      );

      // 「服务与后台」可编辑分组（日志相关已迁到「日志」页）
      var sectionCards = (d.sections || [])
        .map(function (s) {
          return configBlock(s, false);
        })
        .join('');
      var sectionBox = sectionCards
        ? '<div class="error-box warn">下面这些配置会写入 <span class="mono">' +
          esc(d.config_path || '.env') +
          '</span>（自动备份 .env.bak），<b>需要重启 Bot 才会生效</b>；' +
          'WebUI 令牌不在这里改，见上方卡片（可即时生效）。日志相关配置见「日志」页。</div>' +
          '<div class="grid config-grid">' + sectionCards + '</div>'
        : '';

      return versionCard + runtimeCard + versionCardHtml(vc) + sectionBox + tokenCard;
    });
  }

  function afterSettings(root) {
    bindConfigSaves(root);
    bindVersionActions(root);

    var saveBtn = root.querySelector('#save-token');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var input = root.querySelector('#new-token');
        var value = input.value.trim();
        saveBtn.disabled = true;
        send('/settings/token', 'PUT', { token: value })
          .then(function (res) {
            if (value) {
              state.token = value;
              localStorage.setItem('paperbot_token', value);
              document.getElementById('token-input').value = value;
            }
            toast(res.message || '令牌已更新', 'ok');
            render();
          })
          .catch(function (err) {
            toast(err.message || '更新失败', 'err');
          })
          .then(function () {
            saveBtn.disabled = false;
          });
      });
    }
  }

  /* ---------------------------------------------------------------- 日志页 */
  function pageLogs() {
    return api('/settings').then(function (d) {
      var logs = d.logs;
      _logSections = d.log_sections || [];

      // 上方：日志配置（分组摘要卡，点击弹浮窗）
      var configCards = _logSections
        .map(function (s) {
          return configBlock(s, false);
        })
        .join('');

      // 中间：实时日志（轮询）
      var liveCard = card(
        '实时日志',
        '<div class="form-row">' +
          '<div class="field"><label>文件</label><div class="mono" id="live-log-name">' +
          esc(logs.current || '（未启用）') + '</div></div>' +
          '<div class="field"><label>行数</label><select class="input" id="live-log-lines">' +
          '<option value="100">100</option><option value="200" selected>200</option>' +
          '<option value="300">300</option><option value="500">500</option></select></div>' +
          '<label class="switch" title="开启/暂停自动刷新"><input type="checkbox" id="live-log-auto" checked><span class="slider"></span></label>' +
          '<span class="hint">自动刷新</span>' +
          '<button class="btn ghost small" id="live-log-refresh">手动刷新</button>' +
        '</div>' +
        '<div id="live-log" class="log-view spaced">（加载中…）</div>',
        logs.enabled ? '每 2 秒自动刷新，只保留最近若干行' : '日志文件输出已关闭（LOG_FILE_ENABLE=false）'
      );

      // 下方：历史日志文件
      var rows = logs.files.length
        ? logs.files
            .map(function (f) {
              return (
                '<tr><td class="mono">' + esc(f.name) +
                (f.current ? ' ' + badge('当前', 'green') : '') + '</td>' +
                '<td>' + esc(f.size_text) + '</td>' +
                '<td class="hint">' + esc(f.modified_at) + '</td>' +
                '<td><button class="btn ghost small" data-log-view="' + esc(f.name) + '">查看</button>' +
                (f.current ? '' : ' <button class="btn ghost small" data-log-del="' + esc(f.name) + '">删除</button>') +
                '</td></tr>'
              );
            })
            .join('')
        : '<tr><td colspan="4" class="hint">' +
          (logs.enabled ? '还没有日志文件（重启 Bot 后生成）' : '日志文件输出已关闭') + '</td></tr>';

      var historyCard = card(
        '历史日志',
        '<div class="form-row">' +
          '<div class="field"><label>目录</label><div class="mono">' + esc(logs.dir) + '</div></div>' +
          '<div class="field"><label>占用</label><div>' + esc(logs.total_text) + ' · ' +
          logs.files.length + ' 个文件</div></div>' +
          '<button class="btn danger" id="clear-logs">清理全部日志</button>' +
        '</div>' +
        '<div class="table-wrap spaced"><table><thead><tr>' +
        '<th>日志文件</th><th>大小</th><th>最后写入</th><th>操作</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
        '<div id="log-view"></div>',
        logs.enabled ? '本次运行：' + esc(logs.current) : '未启用'
      );

      return (
        (configCards ? '<div class="grid config-grid">' + configCards + '</div>' : '') +
        liveCard + historyCard
      );
    });
  }

  var _logSections = [];

  function refreshLiveLog(root) {
    var box = root.querySelector('#live-log');
    if (!box) return;
    var lines = (root.querySelector('#live-log-lines') || {}).value || '200';
    api('/logs/tail?lines=' + encodeURIComponent(lines))
      .then(function (res) {
        box.textContent = res.content || '（空文件）';
        box.scrollTop = box.scrollHeight;
        var nameEl = root.querySelector('#live-log-name');
        if (nameEl && res.name) nameEl.textContent = res.name;
      })
      .catch(function () { /* 静默：下次轮询再试 */ });
  }

  function afterLogs(root) {
    bindChips(root);
    bindSelectFilters(root);

    on(root, '[data-config-card]', 'click', function (card) {
      var entry = _configItems[card.dataset.configCard];
      if (entry) openConfigModal(entry.item, entry.isAdapter);
    });

    // 实时日志：立即刷一次 + 定时轮询（切页时由 clearPageTimers 统一清理）
    refreshLiveLog(root);
    var auto = root.querySelector('#live-log-auto');
    addPageTimer(
      setInterval(function () {
        if (!document.getElementById('live-log')) return;
        if (!auto || auto.checked) refreshLiveLog(root);
      }, 2000)
    );
    var refresh = root.querySelector('#live-log-refresh');
    if (refresh) refresh.addEventListener('click', function () { refreshLiveLog(root); });
    var linesSel = root.querySelector('#live-log-lines');
    if (linesSel) linesSel.addEventListener('change', function () { refreshLiveLog(root); });

    // 历史日志：查看 / 删除 / 清理全部
    on(root, '[data-log-view]', 'click', function (btn) {
      var box = root.querySelector('#log-view');
      box.innerHTML = '<div class="loading">读取中…</div>';
      api('/settings/logs/' + encodeURIComponent(btn.dataset.logView) + '?lines=300')
        .then(function (res) {
          box.innerHTML =
            '<div class="card"><div class="card-head"><div><div class="card-title">' +
            esc(res.name) + '</div><div class="card-note">' + esc(res.size_text) + ' · 末尾 ' +
            res.lines + ' 行</div></div></div>' +
            '<div class="log-view">' + esc(res.content || '（空文件）') + '</div></div>';
        })
        .catch(function (err) { box.innerHTML = errorBox(err); });
    });
    on(root, '[data-log-del]', 'click', function (btn) {
      var name = btn.dataset.logDel;
      if (!window.confirm('删除日志文件 ' + name + ' ？')) return;
      mutate('/settings/logs/' + encodeURIComponent(name), 'DELETE', undefined,
        { ok: '已删除', err: '删除失败' });
    });
    var clearBtn = root.querySelector('#clear-logs');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        if (!window.confirm('确定清理全部日志吗？（当前运行的日志会被清空）')) return;
        mutate('/settings/logs', 'DELETE', undefined, { ok: '已清理全部日志', err: '清理失败' });
      });
    }
  }

  /* 版本控制按钮组：检查更新 / 更新到最新 / 切换版本 / 重启 */
  function bindVersionActions(root) {
    var checkBtn = root.querySelector('#vc-check');
    if (checkBtn) {
      checkBtn.addEventListener('click', function () {
        checkBtn.disabled = true;
        toast('正在拉取远程版本…');
        send('/version/fetch', 'POST')
          .then(function (data) {
            renderVersionCommits(root, data);
            toast(data.message || '已检查更新', 'ok');
            // 刷新状态卡片上的「落后 / 领先」显示（避免整页闪烁，只更新按钮区）
            var updateBtn = root.querySelector('#vc-update');
            if (updateBtn) updateBtn.disabled = false;
          })
          .catch(function (err) {
            toast('检查更新失败：' + err.message, 'err');
          })
          .then(function () {
            checkBtn.disabled = false;
          });
      });
    }

    var updateBtn = root.querySelector('#vc-update');
    if (updateBtn) {
      updateBtn.addEventListener('click', function () {
        if (!confirm('确认更新到 origin/main 最新版本？（更新后需重启才生效）')) return;
        updateBtn.disabled = true;
        send('/version/update', 'POST', {})
          .then(function (res) {
            var tip = res.message || '已更新';
            if (res.deps_changed) tip += '；依赖有变化，请手动执行 pixi install';
            toast(tip, 'ok');
            render();
          })
          .catch(function (err) {
            toast('更新失败：' + err.message, 'err');
            updateBtn.disabled = false;
          });
      });
    }

    // 事件委托：提交历史里的「切换到此版本」动态渲染，用委托更稳
    root.addEventListener('click', function (ev) {
      var t = ev.target;
      if (!t || !t.id || t.id !== 'vc-switch') return;
      var picked = root.querySelector('input[name="vc-commit"]:checked');
      if (!picked) return toast('请先选一条提交', 'err');
      if (!confirm('确认切换到 ' + picked.value.slice(0, 7) + '？（可用于回滚，更新后需重启生效）')) return;
      t.disabled = true;
      send('/version/update', 'POST', { commit: picked.value })
        .then(function (res) {
          var tip = res.message || '已切换';
          if (res.deps_changed) tip += '；依赖有变化，请手动执行 pixi install';
          toast(tip, 'ok');
          render();
        })
        .catch(function (err) {
          toast('切换失败：' + err.message, 'err');
          t.disabled = false;
        });
    });

    var restartBtn = root.querySelector('#vc-restart');
    if (restartBtn) {
      restartBtn.addEventListener('click', function () {
        if (!confirm('确认重启 Bot？（当前页面会短暂断开，稍后刷新即可）')) return;
        restartBtn.disabled = true;
        send('/version/restart', 'POST')
          .then(function (res) {
            toast(res.message || '正在重启…', 'ok');
          })
          .catch(function () {
            // 进程重启会让请求中断，属正常现象
            toast('进程正在重启，请稍后刷新页面', 'ok');
          });
      });
    }
  }

  /* ------------------------------------------------------------------ LLM 页 */
  function optList(items, selected, placeholder) {
    var head = placeholder ? '<option value="">' + esc(placeholder) + '</option>' : '';
    return (
      head +
      items
        .map(function (item) {
          return (
            '<option value="' + esc(item.value) + '"' +
            (String(item.value) === String(selected || '') ? ' selected' : '') + '>' +
            esc(item.label) + '</option>'
          );
        })
        .join('')
    );
  }

  function pageLlm() {
    return Promise.all([
      api('/llm/status'),
      api('/llm/providers'),
      api('/llm/presets'),
      api('/llm/sessions?limit=50'),
      api('/llm/users?limit=50'),
      api('/llm/groups?limit=50'),
    ]).then(function (res) {
      var st = res[0];
      var prov = res[1];
      var presets = res[2];
      var sessions = res[3];
      var users = res[4];
      var groups = res[5];

      var active = st.active_provider;
      var activeText = active
        ? esc(active.name || 'env') + (active.virtual ? ' ' + badge('来自 .env', 'blue') : '')
        : badge('未配置', 'amber');
      var mask = st.secret_placeholder || '********';

      var overview =
        '<div class="grid">' +
        stat('LLM', st.enable ? '已启用' : '已禁用', st.available ? '调用可用' : '未配置密钥 / 地址') +
        stat('当前服务商', active ? (active.name || 'env') : '（无）', active ? esc(active.model || '默认模型') : '去下面新建一个吧') +
        stat('人设', st.preset_count, '默认：' + (st.default_preset || '未设置')) +
        stat('会话', st.session_count, '累计消息 ' + st.session_messages + ' 条') +
        stat('用户记忆', st.profile_count, '印象 + 关键信息') +
        stat('群记忆', st.group_count || 0, '共同话题 / 群梗') +
        '</div>' +
        '<div class="grid">' +
        stat('流式', st.stream_enable ? '开' : '关', '全局流式输出') +
        stat('上下文', st.context_rounds + ' 轮', '个人 / 群聊上下文') +
        stat('定时总结', (st.summary_enable ? '开' : '关'), st.summary_interval ? ('每 ' + st.summary_interval + ' 秒') : '已关闭') +
        '</div>' +
        '<div class="error-box warn">服务商与人设都存在数据库里，<b>改完即时生效</b>；' +
        '密钥只回显掩码，留着不动就不会被覆盖。若库里没有任何启用服务商，会回落到 ' +
        '<span class="mono">.env</span> 的兜底配置（值 ' +
        (st.env_base_url ? esc(st.env_model || st.env_base_url) : '未设置') + '）。</div>';

      var provRows = prov.items.length
        ? prov.items
            .map(function (p) {
              var actions = '<button class="btn ghost small" data-llm-prov-test="' + esc(p.name) + '">测试</button>';
              if (!p.virtual) {
                actions +=
                  ' <button class="btn ghost small" data-llm-prov-edit="' + esc(p.name) + '">编辑</button>' +
                  ' <button class="btn small danger" data-llm-prov-del="' + esc(p.name) + '">删除</button>';
              }
              return (
                '<tr><td class="mono">' + esc(p.name) + ' ' +
                (p.virtual ? badge('.env 兜底', 'blue') : '') + (p.is_default ? ' ' + badge('默认', 'pink') : '') +
                '</td><td>' + (p.enabled ? badge('启用', 'green') : badge('停用', 'grey')) + '</td>' +
                '<td class="mono hint">' + esc(p.base_url || '-') + '</td>' +
                '<td class="mono">' + esc(p.model || '-') + '</td>' +
                '<td class="hint">' + ((p.models && p.models.length) || 0) + ' 个</td>' +
                '<td>' + (p.api_key_set ? badge('已设置', 'green') : badge('未设置', 'amber')) + '</td>' +
                '<td>' + actions + '</td></tr>'
              );
            })
            .join('')
        : '<tr><td colspan="7" class="hint">还没有服务商（当前只能靠 .env 兜底）</td></tr>';

      var provCard = card(
        '服务商',
          '<div class="table-wrap"><table><thead><tr>' +
          '<th>名称</th><th>状态</th><th>API 地址</th><th>首选模型</th><th>模型数</th><th>密钥</th><th>操作</th>' +
          '</tr></thead><tbody>' + provRows + '</tbody></table></div>' +
          '<div class="form-row spaced">' +
          '<div class="field"><label>名称</label><input class="input" id="llm-prov-name" placeholder="如 deepseek"></div>' +
          '<div class="field" style="flex:1;min-width:240px"><label>API 地址</label>' +
          '<input class="input wide" id="llm-prov-url" placeholder="https://api.deepseek.com/v1"></div>' +
          '<div class="field" style="flex:1;min-width:200px"><label>API Key</label>' +
          '<input class="input wide" id="llm-prov-key" type="password" placeholder="留空 = 不改（新建时为空）" autocomplete="off"></div>' +
          '<div class="field"><label>温度</label><input class="input short" id="llm-prov-temp" placeholder="留空默认"></div>' +
          '<div class="field"><label>设为默认</label><select class="input" id="llm-prov-default">' +
          '<option value="1">是（推荐）</option><option value="0">否</option></select></div>' +
          '<button class="btn" data-llm-prov-save>保存服务商</button>' +
          '</div>' +
          '<div class="field spaced"><label>模型（优先 + 备用，最多 3 个，拖动排序）</label>' +
          '<div class="hint">填好地址和 Key 后点「获取可用模型」，从下拉里选模型加入列表；排在最前的作为首选，失败自动切下一个。</div>' +
          '<div class="form-row" style="align-items:flex-end">' +
          '<div class="field" style="flex:1;min-width:220px"><input class="input wide" id="llm-models-filter" data-filter-for="llm-models-select" placeholder="输入以筛选模型…">' +
          '<select class="input wide" id="llm-models-select"><option value="">（先获取模型）</option></select></div>' +
          '<button class="btn ghost" data-llm-models-fetch>获取可用模型</button>' +
          '<button class="btn ghost" data-llm-models-add>加入列表</button>' +
          '</div>' +
          '<ol class="model-list" id="llm-models-list"></ol>' +
          '<div class="form-row spaced">' +
          '<button class="btn ghost small" data-llm-models-up>↑ 上移</button>' +
          '<button class="btn ghost small" data-llm-models-down>↓ 下移</button>' +
          '<button class="btn ghost small danger" data-llm-models-remove>移除选中</button>' +
          '</div></div>' +
          '<div class="hint spaced">「测试」会真实发一条 <span class="mono">ping</span> 并返回耗时；' +
          '编辑时密钥留空 = 保持原值（要换就直接填新的）。</div>',
        prov.count + ' 个（含 .env 兜底虚拟项）'
      );

      var presetOptions = optList(
        presets.items.map(function (p) { return { value: p.name, label: p.display_name || p.name }; }),
        '',
        '（用全局默认人设）'
      );

      var chatCard = card(
        '试聊（不带上下文）',
        '<div class="form-row">' +
          '<div class="field"><label>人设</label><select class="input" id="llm-chat-preset" style="min-width:160px">' +
          presetOptions + '</select></div>' +
          '<div class="field" style="flex:1;min-width:260px"><label>说点什么</label>' +
          '<input class="input wide" id="llm-chat-prompt" placeholder="你好呀"></div>' +
          '<button class="btn" data-llm-chat>发送</button>' +
          '</div>' +
          '<div id="llm-chat-result" class="spaced"></div>' +
          '<div class="hint spaced">这条不会写进会话、也不碰记忆，纯用来验证服务商 / 人设配得对不对。' +
          '密钥掩码占位是 <span class="mono">' + esc(mask) + '</span>。</div>'
      );

      var srcLabel = presets.source_labels || {};
      var presetRows = presets.items.length
        ? presets.items
            .map(function (p) {
              return (
                '<tr><td class="mono">' + esc(p.name) + (p.name === st.default_preset ? ' ' + badge('默认', 'pink') : '') + '</td>' +
                '<td>' + esc(p.display_name) + '</td>' +
                '<td>' + badge(srcLabel[p.source] || p.source, p.source === 'manual' ? 'blue' : 'grey') + '</td>' +
                '<td>' + (p.enabled ? badge('启用', 'green') : badge('停用', 'grey')) + '</td>' +
                '<td class="hint">' + p.prompt_length + ' 字</td>' +
                '<td class="hint">' + esc(p.updated_at || '-') + '</td>' +
                '<td><button class="btn ghost small" data-llm-preset-edit="' + esc(p.name) + '">编辑</button>' +
                ' <button class="btn ghost small" data-llm-preset-export="' + esc(p.name) + '">导出</button>' +
                ' <button class="btn small danger" data-llm-preset-del="' + esc(p.name) + '">删除</button></td></tr>'
              );
            })
            .join('')
        : '<tr><td colspan="7" class="hint">还没有人设（插件里可以直接传 system prompt）</td></tr>';

      var presetCard = card(
        '人设',
        '<div class="table-wrap"><table><thead><tr>' +
          '<th>名字</th><th>显示名</th><th>来源</th><th>状态</th><th>提示词</th><th>更新</th><th>操作</th>' +
          '</tr></thead><tbody>' + presetRows + '</tbody></table></div>' +
          '<div class="error-box warn spaced">人设文件格式：<span class="mono">---</span> 包裹的 YAML 头（' +
          '<span class="mono">name</span> / <span class="mono">display_name</span> / ' +
          '<span class="mono">description</span> / <span class="mono">params</span>）后面接正文，正文即提示词。' +
          'WebUI 改过的会标成「人工」，之后导入同名文件<b>不会</b>覆盖它。</div>' +
          '<div class="form-row spaced">' +
          '<div class="field"><label>名字（唯一键）</label><input class="input" id="llm-preset-name" placeholder="如 paper_white"></div>' +
          '<div class="field"><label>显示名</label><input class="input" id="llm-preset-display" placeholder="白纸"></div>' +
          '<div class="field" style="flex:1;min-width:200px"><label>简介</label>' +
          '<input class="input wide" id="llm-preset-desc" placeholder="一句话介绍"></div>' +
          '<div class="field"><label>状态</label><select class="input" id="llm-preset-enabled">' +
          '<option value="1">启用</option><option value="0">停用</option></select></div>' +
          '</div>' +
          '<div class="field spaced"><label>系统提示词（正文）</label>' +
          '<textarea class="input wide" id="llm-preset-prompt" rows="6" placeholder="你是…"></textarea></div>' +
          '<div class="form-row spaced">' +
          '<button class="btn" data-llm-preset-save>保存人设</button>' +
          '<button class="btn ghost" data-llm-preset-reset>清空表单</button></div>' +
          '<div class="field spaced"><label>导入人设文本</label>' +
          '<textarea class="input wide" id="llm-preset-import" rows="4" placeholder="把 .md 内容粘进来，或下面选文件"></textarea></div>' +
          '<div class="form-row spaced">' +
          '<input type="file" id="llm-preset-file" accept=".md,.txt,.markdown">' +
          '<div class="field"><label>同名已存在</label><select class="input" id="llm-preset-force">' +
          '<option value="0">跳过（安全）</option><option value="1">强制覆盖</option></select></div>' +
          '<button class="btn" data-llm-preset-import>导入</button>' +
          '</div>',
        presets.count + ' 个 · 默认「' + esc(st.default_preset || '未设置') + '」'
      );

      var sessionRows = sessions.items.length
        ? sessions.items
            .map(function (s) {
              return (
                '<tr><td>' + badge(s.scope_label, s.scope_type === 'group' ? 'blue' : 'pink') + '</td>' +
                '<td class="mono">' + esc(s.scope_id) + '</td>' +
                '<td class="mono dim">' + esc(s.plugin_name || '（共享）') + '</td>' +
                '<td class="mono">' + esc(s.preset_name || '-') + '</td>' +
                '<td class="mono dim">' + esc(s.provider_name || '-') + '</td>' +
                '<td class="hint">' + s.message_count + ' 条（用户 ' + s.user_message_count + '）</td>' +
                '<td class="hint">' + esc(s.updated_at || '-') + '</td>' +
                '<td><button class="btn ghost small" data-llm-session-msg="' + s.id + '">消息</button>' +
                ' <button class="btn ghost small" data-llm-session-set="' + s.id + '">换人设</button>' +
                ' <button class="btn small danger" data-llm-session-del="' + s.id + '">清空</button></td></tr>'
              );
            })
            .join('')
        : '<tr><td colspan="8" class="hint">还没有会话（聊过天才会出现）</td></tr>';

      var sessionCard = card(
        '会话（上下文）',
        '<div class="error-box warn">作用域就是「谁在聊」：<b>个人</b>是私聊 / 单人对话，' +
        '<b>群聊</b>是整个群共享；<span class="mono">plugin_name</span> 为空表示各插件共用一个上下文，' +
        '填了就是按插件隔离。清空只删聊天记录，不动长期记忆。</div>' +
        '<div class="table-wrap"><table><thead><tr>' +
          '<th>作用域</th><th>ID</th><th>插件</th><th>人设</th><th>服务商</th><th>消息</th><th>更新</th><th>操作</th>' +
          '</tr></thead><tbody>' + sessionRows + '</tbody></table></div>' +
        '<div id="llm-session-view" class="spaced"></div>',
        sessions.count + ' 个（最多列 50）'
      );

      var userRows = users.items.length
        ? users.items
            .map(function (u) {
              return (
                '<tr><td class="mono">' + esc(u.user_id) + '</td>' +
                '<td class="hint">' + u.message_count + ' 条</td>' +
                '<td class="hint">' + esc((u.impression || '').slice(0, 40) || '（还没印象）') + '</td>' +
                '<td class="hint">' + (u.fact_count || 0) + ' 条</td>' +
                '<td class="hint">' + esc(u.updated_at || '-') + '</td>' +
                '<td><button class="btn ghost small" data-llm-user-view="' + esc(u.user_id) + '">查看</button>' +
                ' <button class="btn small danger" data-llm-user-del="' + esc(u.user_id) + '">清空记忆</button></td></tr>'
              );
            })
            .join('')
        : '<tr><td colspan="6" class="hint">还没有用户记忆</td></tr>';

      var userCard = card(
        '用户长期记忆',
        '<div class="error-box warn">印象是攒够 ' + st.impression_every +
        ' 条消息自动提炼的（受 <span class="mono">LLM_MEMORY_ENABLE</span> 控制）；关键信息可以自己加，' +
        '自动提炼不会覆盖人工写的。最多注入 ' + st.max_facts + ' 条关键信息。</div>' +
        '<div class="table-wrap"><table><thead><tr>' +
          '<th>用户</th><th>消息</th><th>印象</th><th>关键信息</th><th>更新</th><th>操作</th>' +
          '</tr></thead><tbody>' + userRows + '</tbody></table></div>' +
        '<div class="form-row spaced">' +
          '<div class="field"><label>用户 QQ</label><input class="input" id="llm-user-id" placeholder="如 10001"></div>' +
          '<button class="btn ghost" data-llm-user-open>查看 / 编辑</button>' +
        '</div>' +
        '<div id="llm-user-view" class="spaced"></div>',
        users.count + ' 位用户（最多列 50）'
      );

      var groupRows = groups.items.length
        ? groups.items
            .map(function (g) {
              return (
                '<tr><td class="mono">' + esc(g.group_id) + '</td>' +
                '<td>' + esc((g.memory || '').slice(0, 120) || '（暂无）') + '</td>' +
                '<td class="hint">' + g.message_count + ' 条</td>' +
                '<td class="hint">' + esc(g.updated_at || '-') + '</td></tr>'
              );
            })
            .join('')
        : '<tr><td colspan="4" class="hint">还没有群记忆（聊一阵后由定时总结自动生成）</td></tr>';

      var groupCard = card(
        '群记忆',
        '<div class="error-box warn">由后台定时任务（' + (st.summary_interval || 1800) +
        ' 秒一轮）从群聊流水增量提炼的共同话题 / 群梗，可在「数据库管理」页查看 ' +
        '<span class="mono">chat_message</span> 原始流水。</div>' +
        '<div class="table-wrap"><table><thead><tr>' +
          '<th>群</th><th>共同记忆</th><th>累计消息</th><th>更新</th>' +
          '</tr></thead><tbody>' + groupRows + '</tbody></table></div>',
        groups.count + ' 个群（最多列 50）'
      );

      // 运行参数（.env 兜底 + 定时总结）：后端已把这组从「系统配置」页挪到 LLM 页，
      // 字段结构与 /api/config 的 section 完全一致，直接复用 configBlock（点击弹浮窗编辑）
      var envSections = st.env_sections || [];
      envSections.forEach(function (s) {
        _configItems[s.id || s.name] = { item: s, isAdapter: false };
      });
      var envCards = envSections
        .map(function (s) {
          return configBlock(s, false);
        })
        .join('');
      var envBox = envCards
        ? '<div class="error-box warn">运行参数（写入 <span class="mono">.config/main/.env</span>，' +
          '修改后点右上角「重载」（可热更项立即生效）或重启 Bot 生效）。' +
          '服务商 / 人设存在数据库里，<b>改完即时生效</b>；这里只作 .env 兜底。</div>' +
          '<div class="grid config-grid">' + envCards + '</div>'
        : '';

      var html =
        overview + provCard + chatCard + presetCard + sessionCard + userCard + groupCard + envBox;
      return html;

    });
  }

  function fillProviderForm(root, p) {
    root.querySelector('#llm-prov-name').value = p.name || '';
    root.querySelector('#llm-prov-url').value = p.base_url || '';
    root.querySelector('#llm-prov-key').value = p.api_key_set ? (p.api_key || '') : '';
    root.querySelector('#llm-prov-temp').value = p.temperature === null || p.temperature === undefined ? '' : p.temperature;
    root.querySelector('#llm-prov-default').value = p.is_default ? '1' : '0';
    setModelList(root, p.models && p.models.length ? p.models : (p.model ? [p.model] : []));
    toast('已载入「' + (p.name || '') + '」，密钥留空即保持原值', 'ok');
  }

  var MODEL_STORE = {};
  function setModelList(root, models) {
    MODEL_STORE = (models || []).slice(0, 3);
    renderModelList(root);
  }
  function renderModelList(root) {
    var list = root.querySelector('#llm-models-list');
    if (!list) return;
    list.innerHTML = MODEL_STORE.length
      ? MODEL_STORE.map(function (m, i) {
          return (
            '<li data-llm-model-idx="' + i + '"' + (i === 0 ? ' class="first"' : '') + '>' +
            '<span class="mono">' + esc(m) + '</span>' +
            (i === 0 ? ' <span class="hint">（首选）</span>' : '') + '</li>'
          );
        }).join('')
      : '<li class="hint">还没有添加模型（至少选 1 个）</li>';
    list.querySelectorAll('li[data-llm-model-idx]').forEach(function (li) {
      li.addEventListener('click', function () {
        list.querySelectorAll('li').forEach(function (x) { x.classList.remove('selected'); });
        li.classList.add('selected');
      });
    });
  }
  function selectedModelIdx() {
    var sel = document.querySelector('#llm-models-list li.selected');
    return sel && sel.dataset.llmModelIdx !== undefined ? parseInt(sel.dataset.llmModelIdx, 10) : -1;
  }
  function fillModelSelect(root, models) {
    var sel = root.querySelector('#llm-models-select');
    if (!sel) return;
    var html = '<option value="">（' + models.length + ' 个可用，点选加入）</option>';
    html += models.map(function (m) { return '<option value="' + esc(m) + '">' + esc(m) + '</option>'; }).join('');
    sel.innerHTML = html;
  }

  function fillPresetForm(root, name, d) {
    root.querySelector('#llm-preset-name').value = name || '';
    root.querySelector('#llm-preset-display').value = (d && d.display_name) || '';
    root.querySelector('#llm-preset-desc').value = (d && d.description) || '';
    root.querySelector('#llm-preset-prompt').value = (d && d.text) || '';
    root.querySelector('#llm-preset-enabled').value = d && d.enabled === false ? '0' : '1';
  }

  function renderSessionMessages(root, box, id) {
    box.innerHTML = '<div class="loading">读取中…</div>';
    api('/llm/sessions/' + id + '/messages?limit=100')
      .then(function (d) {
        var s = d.session;
        var rows = d.items.length
          ? d.items
              .map(function (m) {
                return (
                  '<tr><td class="mono">' + esc(m.role) + '</td><td class="hint">' + esc(m.created_at || '-') + '</td>' +
                  '<td>' + esc(m.content || '').slice(0, 500) + '</td></tr>'
                );
              })
              .join('')
          : '<tr><td colspan="3" class="hint">这个会话还没消息</td></tr>';
        box.innerHTML =
          '<div class="card"><div class="card-head"><div>' +
          '<div class="card-title">会话 #' + id + ' · ' + esc(s.scope_label) + ' ' + esc(s.scope_id) +
          ' <span class="mono dim">' + esc(s.plugin_name || '（共享）') + '</span></div>' +
          '<div class="card-note">人设 ' + esc(s.preset_name || '（全局默认）') + ' · 服务商 ' +
          esc(s.provider_name || '（默认）') + ' · ' + s.message_count + ' 条</div>' +
          '</div></div><div class="table-wrap"><table><thead><tr>' +
          '<th>角色</th><th>时间</th><th>内容</th></tr></thead><tbody>' + rows + '</tbody></table></div></div>';
      })
      .catch(function (err) {
        box.innerHTML = errorBox(err);
      });
  }

  function renderSessionEditor(root, box, id) {
    box.innerHTML = '<div class="loading">读取中…</div>';
    api('/llm/presets').then(function (d) {
      var opts = optList(
        d.items.map(function (p) { return { value: p.name, label: p.display_name || p.name }; }),
        '',
        '（回落全局默认）'
      );
      box.innerHTML =
        '<div class="error-box warn">给会话 #' + id + ' 单独指定人设 / 服务商（空 = 回落全局默认）。</div>' +
        '<div class="form-row">' +
        '<div class="field"><label>人设</label><select class="input" id="llm-sess-preset" style="min-width:160px">' + opts + '</select></div>' +
        '<div class="field"><label>服务商名</label><input class="input" id="llm-sess-provider" placeholder="留空 = 默认"></div>' +
        '<button class="btn" data-llm-session-save="' + id + '">保存</button>' +
        '<button class="btn ghost" data-llm-session-cancel>收起</button></div>';
    });
  }

  function renderUserView(root, box, uid) {
    box.innerHTML = '<div class="loading">读取中…</div>';
    api('/llm/users/' + encodeURIComponent(uid))
      .then(function (d) {
        var u = d.item;
        var rows = u.facts.length
          ? u.facts
              .map(function (f) {
                return (
                  '<tr><td class="mono">' + esc(f.key) + '</td><td>' + esc(f.value) + '</td>' +
                  '<td>' + badge(f.source, f.source === 'manual' ? 'blue' : 'grey') + '</td>' +
                  '<td class="hint">' + esc(f.updated_at || '-') + '</td>' +
                  '<td><button class="btn small danger" data-llm-fact-del="' + esc(uid) + '|' + esc(f.key) + '">删除</button></td></tr>'
                );
              })
              .join('')
          : '<tr><td colspan="5" class="hint">还没有关键信息</td></tr>';
        box.innerHTML =
          '<div class="card"><div class="card-head"><div>' +
          '<div class="card-title">用户 ' + esc(uid) + '</div>' +
          '<div class="card-note">累计 ' + u.message_count + ' 条消息 · 印象更新于 ' + esc(u.impression_at || '（未提炼）') + '</div>' +
          '</div></div>' +
          '<div class="field"><label>印象（给模型看的人物画像）</label>' +
          '<textarea class="input wide" id="llm-user-impression" rows="4">' + esc(u.impression || '') + '</textarea></div>' +
          '<div class="form-row spaced"><button class="btn" data-llm-imp-save="' + esc(uid) + '">保存印象</button>' +
          '<button class="btn ghost" data-llm-user-close>收起</button></div>' +
          '<div class="table-wrap spaced"><table><thead><tr>' +
          '<th>键</th><th>值</th><th>来源</th><th>更新</th><th>操作</th></tr></thead><tbody>' + rows +
          '</tbody></table></div>' +
          '<div class="form-row spaced">' +
          '<div class="field"><label>键</label><input class="input" id="llm-fact-key" placeholder="如 城市"></div>' +
          '<div class="field" style="flex:1;min-width:200px"><label>值</label><input class="input wide" id="llm-fact-value" placeholder="如 杭州"></div>' +
          '<button class="btn" data-llm-fact-add="' + esc(uid) + '">写入关键信息</button></div></div>';
      })
      .catch(function (err) {
        box.innerHTML = errorBox(err);
      });
  }

  function downloadText(filename, text) {
    var blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function afterLlm(root) {
    function val(sel) {
      var el = root.querySelector(sel);
      return el ? el.value : '';
    }

    function each(sel, fn) {
      root.querySelectorAll(sel).forEach(function (el) {
        el.addEventListener('click', function () { fn(el); });
      });
    }

    // 长下拉筛选（模型选择）
    root.querySelectorAll('[data-filter-for]').forEach(function (box) {
      bindSelectFilter(root, box, root.querySelector('#' + box.dataset.filterFor));
    });

    /* ---- 运行参数（.env 兜底 + 定时总结）：点击摘要卡弹浮窗编辑 ---- */
    // 这组卡片由 pageLlm 渲染一次，这里直接绑一次即可（不要再在末尾粘一遍）
    on(root, '[data-config-card]', 'click', function (card) {
      var entry = _configItems[card.dataset.configCard];
      if (!entry) return;
      openConfigModal(entry.item, entry.isAdapter);
    });
    // chips（list 字段）/ 长下拉过滤 / 保存按钮：与系统配置页同款绑定
    bindChips(root);
    bindSelectFilters(root);
    bindConfigSaves(root);

    /* ---- 服务商 ---- */
    var saveProv = root.querySelector('[data-llm-prov-save]');
    if (saveProv) {
      saveProv.addEventListener('click', function () {
        var name = val('#llm-prov-name').trim();
        if (!name) {
          toast('先给服务商起个名字吧', 'err');
          return;
        }
        var temp = val('#llm-prov-temp').trim();
        var body = {
          base_url: val('#llm-prov-url').trim(),
          is_default: val('#llm-prov-default') === '1',
        };
        if (MODEL_STORE.length) {
          body.models = MODEL_STORE;
          body.model = MODEL_STORE[0];
          body.priority = 0;
        }
        var keyValue = val('#llm-prov-key');
        if (keyValue) body.api_key = keyValue; // 留空 = 不改动
        if (temp) body.temperature = parseFloat(temp);
        saveProv.disabled = true;
        send('/llm/providers/' + encodeURIComponent(name), 'PUT', body)
          .then(function (res) {
            toast(res.message || '已保存', 'ok');
            render();
          })
          .catch(function (err) {
            toast('保存失败：' + err.message, 'err');
            saveProv.disabled = false;
          });
      });
    }

    each('[data-llm-prov-edit]', function (btn) {
      api('/llm/providers').then(function (d) {
        var found = d.items.filter(function (p) { return p.name === btn.dataset.llmProvEdit; })[0];
        if (found) fillProviderForm(root, found);
      });
    });

    each('[data-llm-prov-test]', function (btn) {
      var name = btn.dataset.llmProvTest;
      btn.disabled = true;
      toast('正在测试 ' + name + ' …');
      send('/llm/providers/' + encodeURIComponent(name) + '/test', 'POST')
        .then(function (res) {
          toast(res.message + '：' + String(res.text || '').slice(0, 30), 'ok');
          var box = root.querySelector('#llm-chat-result');
          if (box) {
            box.innerHTML =
              '<div class="card"><div class="card-head"><div><div class="card-title">连接测试 · ' +
              esc(res.provider || name) + '</div><div class="card-note">模型 ' + esc(res.model || '-') +
              ' · 耗时 ' + res.elapsed_ms + 'ms</div></div></div><div class="card-body">' +
              '<div class="mono">' + esc(res.text || '（空回复）') + '</div></div></div>';
          }
        })
        .catch(function (err) {
          toast('测试失败：' + err.message, 'err');
        })
        .then(function () { btn.disabled = false; });
    });

    each('[data-llm-prov-del]', function (btn) {
      var name = btn.dataset.llmProvDel;
      if (!window.confirm('删除服务商「' + name + '」？')) return;
      send('/llm/providers/' + encodeURIComponent(name), 'DELETE')
        .then(function (res) {
          toast(res.message || '已删除', 'ok');
          render();
        })
        .catch(function (err) { toast('删除失败：' + err.message, 'err'); });
    });

    /* ---- 多模型（获取可用模型 + 优先/备用排序） ---- */
    var fetchModels = root.querySelector('[data-llm-models-fetch]');
    if (fetchModels) {
      fetchModels.addEventListener('click', function () {
        var url = val('#llm-prov-url').trim();
        var key = val('#llm-prov-key');
        fetchModels.disabled = true;
        toast('正在拉取模型列表…');
        send('/llm/models/fetch', 'POST', { base_url: url, api_key: key })
          .then(function (res) {
            fillModelSelect(root, res.models || []);
            toast(res.message || '拉取成功', 'ok');
          })
          .catch(function (err) {
            toast('拉取失败：' + err.message, 'err');
          })
          .then(function () { fetchModels.disabled = false; });
      });
    }

    var addModel = root.querySelector('[data-llm-models-add]');
    if (addModel) {
      addModel.addEventListener('click', function () {
        var sel = root.querySelector('#llm-models-select');
        var m = sel ? sel.value : '';
        if (!m) { toast('先从下拉里选一个模型', 'err'); return; }
        if (MODEL_STORE.indexOf(m) !== -1) { toast('这个模型已经在列表里了', 'err'); return; }
        if (MODEL_STORE.length >= 3) { toast('最多 3 个模型（优先 + 备用）', 'err'); return; }
        MODEL_STORE.push(m);
        renderModelList(root);
        toast('已加入：' + m, 'ok');
      });
    }

    var moveUp = root.querySelector('[data-llm-models-up]');
    if (moveUp) {
      moveUp.addEventListener('click', function () {
        var i = selectedModelIdx();
        if (i <= 0) { toast('先选中一个可上移的模型', 'err'); return; }
        var t = MODEL_STORE[i - 1]; MODEL_STORE[i - 1] = MODEL_STORE[i]; MODEL_STORE[i] = t;
        renderModelList(root);
      });
    }

    var moveDown = root.querySelector('[data-llm-models-down]');
    if (moveDown) {
      moveDown.addEventListener('click', function () {
        var i = selectedModelIdx();
        if (i < 0 || i >= MODEL_STORE.length - 1) { toast('先选中一个可下移的模型', 'err'); return; }
        var t = MODEL_STORE[i + 1]; MODEL_STORE[i + 1] = MODEL_STORE[i]; MODEL_STORE[i] = t;
        renderModelList(root);
      });
    }

    var removeModel = root.querySelector('[data-llm-models-remove]');
    if (removeModel) {
      removeModel.addEventListener('click', function () {
        var i = selectedModelIdx();
        if (i < 0) { toast('先选中要移除的模型', 'err'); return; }
        MODEL_STORE.splice(i, 1);
        renderModelList(root);
      });
    }

    /* ---- 试聊 ---- */
    var chatBtn = root.querySelector('[data-llm-chat]');
    if (chatBtn) {
      chatBtn.addEventListener('click', function () {
        var prompt = val('#llm-chat-prompt').trim();
        if (!prompt) {
          toast('先说点什么呀', 'err');
          return;
        }
        var box = root.querySelector('#llm-chat-result');
        box.innerHTML = '<div class="loading">等待模型回复…</div>';
        chatBtn.disabled = true;
        send('/llm/chat', 'POST', { prompt: prompt, preset: val('#llm-chat-preset') })
          .then(function (res) {
            box.innerHTML =
              '<div class="card"><div class="card-head"><div><div class="card-title">回复</div>' +
              '<div class="card-note">' + res.message + ' · ' + String(res.text || '').length + ' 字</div>' +
              '</div></div><div class="card-body"><div class="markdown-like">' +
              esc(res.text || '（空回复）') + '</div></div></div>';
          })
          .catch(function (err) {
            box.innerHTML = errorBox(err);
          })
          .then(function () { chatBtn.disabled = false; });
      });
    }

    /* ---- 人设 ---- */
    var presetSave = root.querySelector('[data-llm-preset-save]');
    if (presetSave) {
      presetSave.addEventListener('click', function () {
        var name = val('#llm-preset-name').trim();
        if (!name) {
          toast('人设名不能为空（插件里就是靠它引用的）', 'err');
          return;
        }
        presetSave.disabled = true;
        send('/llm/presets/' + encodeURIComponent(name), 'PUT', {
          display_name: val('#llm-preset-display').trim(),
          description: val('#llm-preset-desc').trim(),
          system_prompt: val('#llm-preset-prompt'),
          enabled: val('#llm-preset-enabled') === '1',
        })
          .then(function (res) {
            toast(res.message || '已保存', 'ok');
            render();
          })
          .catch(function (err) {
            toast('保存失败：' + err.message, 'err');
            presetSave.disabled = false;
          });
      });
    }

    var presetReset = root.querySelector('[data-llm-preset-reset]');
    if (presetReset) {
      presetReset.addEventListener('click', function () {
        fillPresetForm(root, '', null);
        toast('表单已清空，可以写新的人设啦');
      });
    }

    each('[data-llm-preset-edit]', function (btn) {
      var name = btn.dataset.llmPresetEdit;
      api('/llm/presets/' + encodeURIComponent(name)).then(function (d) {
        fillPresetForm(root, name, d.item);
        toast('已载入人设「' + name + '」（' + (d.item.prompt_length || 0) + ' 字）', 'ok');
      });
    });

    each('[data-llm-preset-export]', function (btn) {
      var name = btn.dataset.llmPresetExport;
      api('/llm/presets/' + encodeURIComponent(name) + '/export')
        .then(function (text) {
          downloadText(name + '.md', typeof text === 'string' ? text : JSON.stringify(text));
          toast('已导出 ' + name + '.md', 'ok');
        })
        .catch(function (err) { toast('导出失败：' + err.message, 'err'); });
    });

    each('[data-llm-preset-del]', function (btn) {
      var name = btn.dataset.llmPresetDel;
      if (!window.confirm('删除人设「' + name + '」？引用了它的话会回落到全局默认。')) return;
      send('/llm/presets/' + encodeURIComponent(name), 'DELETE')
        .then(function (res) {
          toast(res.message || '已删除', 'ok');
          render();
        })
        .catch(function (err) { toast('删除失败：' + err.message, 'err'); });
    });

    var fileInput = root.querySelector('#llm-preset-file');
    if (fileInput) {
      fileInput.addEventListener('change', function () {
        var file = fileInput.files && fileInput.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function () {
          root.querySelector('#llm-preset-import').value = String(reader.result || '');
          toast('已读入 ' + file.name, 'ok');
        };
        reader.readAsText(file, 'utf-8');
      });
    }

    var importBtn = root.querySelector('[data-llm-preset-import]');
    if (importBtn) {
      importBtn.addEventListener('click', function () {
        var text = val('#llm-preset-import');
        if (!text.trim()) {
          toast('先粘贴内容或选个文件吧', 'err');
          return;
        }
        importBtn.disabled = true;
        send('/llm/presets/import', 'POST', {
          text: text,
          filename: fileInput && fileInput.files[0] ? fileInput.files[0].name : '',
          force: val('#llm-preset-force') === '1',
        })
          .then(function (res) {
            toast(res.message || '导入完成', res.ok ? 'ok' : 'err');
            if (res.ok) render();
            else importBtn.disabled = false;
          })
          .catch(function (err) {
            toast('导入失败：' + err.message, 'err');
            importBtn.disabled = false;
          });
      });
    }

    /* ---- 会话 ---- */
    var sessBox = root.querySelector('#llm-session-view');
    each('[data-llm-session-msg]', function (btn) {
      renderSessionMessages(root, sessBox, parseInt(btn.dataset.llmSessionMsg, 10));
    });
    each('[data-llm-session-set]', function (btn) {
      renderSessionEditor(root, sessBox, parseInt(btn.dataset.llmSessionSet, 10));
    });
    each('[data-llm-session-del]', function (btn) {
      var id = btn.dataset.llmSessionDel;
      if (!window.confirm('清空会话 #' + id + ' 的聊天记录？（长期记忆不受影响）')) return;
      send('/llm/sessions/' + id, 'DELETE')
        .then(function (res) {
          toast(res.message || '已清空', 'ok');
          render();
        })
        .catch(function (err) { toast('清空失败：' + err.message, 'err'); });
    });

    /* ---- 用户记忆 ---- */
    var userBox = root.querySelector('#llm-user-view');
    each('[data-llm-user-view]', function (btn) {
      renderUserView(root, userBox, btn.dataset.llmUserView);
    });
    var userOpen = root.querySelector('[data-llm-user-open]');
    if (userOpen) {
      userOpen.addEventListener('click', function () {
        var uid = val('#llm-user-id').trim();
        if (!uid) {
          toast('填个 QQ 号吧', 'err');
          return;
        }
        renderUserView(root, userBox, uid);
      });
    }
    each('[data-llm-user-del]', function (btn) {
      var uid = btn.dataset.llmUserDel;
      if (!window.confirm('清空用户 ' + uid + ' 的印象与全部关键信息？')) return;
      send('/llm/users/' + encodeURIComponent(uid), 'DELETE')
        .then(function (res) {
          toast(res.message || '已清空', 'ok');
          render();
        })
        .catch(function (err) { toast('清空失败：' + err.message, 'err'); });
    });

    /* 动态区域里的按钮（用事件委托，渲染后依然有效） */
    root.addEventListener('click', function (ev) {
      var t = ev.target;
      if (!t || !t.dataset) return;

      if (t.dataset.llmSessionSave) {
        var sid = t.dataset.llmSessionSave;
        t.disabled = true;
        send('/llm/sessions/' + sid, 'PUT', {
          preset_name: val('#llm-sess-preset'),
          provider_name: val('#llm-sess-provider').trim(),
        })
          .then(function (res) {
            toast(res.message || '已更新', 'ok');
            render();
          })
          .catch(function (err) {
            toast('更新失败：' + err.message, 'err');
            t.disabled = false;
          });
      }

      if (t.dataset.llmSessionCancel || t.dataset.llmUserClose) {
        (t.dataset.llmSessionCancel ? sessBox : userBox).innerHTML = '';
      }

      if (t.dataset.llmImpSave) {
        var uid = t.dataset.llmImpSave;
        t.disabled = true;
        send('/llm/users/' + encodeURIComponent(uid) + '/impression', 'PUT', {
          impression: val('#llm-user-impression'),
        })
          .then(function (res) {
            toast(res.message || '印象已更新', 'ok');
            t.disabled = false;
          })
          .catch(function (err) {
            toast('保存失败：' + err.message, 'err');
            t.disabled = false;
          });
      }

      if (t.dataset.llmFactAdd) {
        var fuid = t.dataset.llmFactAdd;
        var key = val('#llm-fact-key').trim();
        if (!key) {
          toast('键不能为空', 'err');
          return;
        }
        t.disabled = true;
        send('/llm/users/' + encodeURIComponent(fuid) + '/facts', 'PUT', {
          key: key,
          value: val('#llm-fact-value'),
        })
          .then(function (res) {
            toast(res.message || '已写入', 'ok');
            renderUserView(root, userBox, fuid);
          })
          .catch(function (err) {
            toast('写入失败：' + err.message, 'err');
            t.disabled = false;
          });
      }

      if (t.dataset.llmFactDel) {
        var parts = String(t.dataset.llmFactDel).split('|');
        var duid = parts[0];
        var dkey = parts.slice(1).join('|');
        if (!window.confirm('删除「' + dkey + '」？')) return;
        send('/llm/users/' + encodeURIComponent(duid) + '/facts?key=' + encodeURIComponent(dkey), 'DELETE')
          .then(function (res) {
            toast(res.message || '已删除', 'ok');
            renderUserView(root, userBox, duid);
          })
          .catch(function (err) { toast('删除失败：' + err.message, 'err'); });
      }
    });
  }

  /* ------------------------------------------------------------ 路由与启动 */

  var PAGES = {
    status: pageStatus,
    config: pageConfig,
    permissions: pagePermissions,
    plugins: pagePlugins,
    llm: pageLlm,
    database: pageDatabase,
    commands: pageCommands,
    logs: pageLogs,
    settings: pageSettings,
  };

  var AFTER = {
    status: afterStatus,
    config: afterConfig,
    permissions: afterPermissions,
    plugins: afterPlugins,
    llm: afterLlm,
    database: afterDatabase,
    commands: afterCommands,
    logs: afterLogs,
    settings: afterSettings,
  };

  function buildNav() {
    var nav = document.getElementById('nav');
    nav.innerHTML = MENU.map(function (item) {
      return (
        '<button class="nav-item" data-id="' + item.id + '">' +
        '<span class="icon">' + item.icon + '</span><span class="label">' + esc(item.label) + '</span></button>'
      );
    }).join('');
    nav.querySelectorAll('.nav-item').forEach(function (btn) {
      btn.addEventListener('click', function () {
        location.hash = '#/' + btn.dataset.id;
      });
    });
  }

  function currentPageId() {
    var id = (location.hash || '').replace(/^#\/?/, '');
    for (var i = 0; i < MENU.length; i++) {
      if (MENU[i].id === id) return id;
    }
    return MENU[0].id;
  }

  // 页面级定时器登记表：切页 / 刷新时统一清理，避免后台空跑
  var _pageTimers = [];

  function addPageTimer(timer) {
    _pageTimers.push(timer);
    return timer;
  }

  function clearPageTimers() {
    _pageTimers.forEach(function (t) {
      clearInterval(t);
      clearTimeout(t);
    });
    _pageTimers = [];
  }

  function render() {
    var id = currentPageId();
    var page = MENU.filter(function (m) {
      return m.id === id;
    })[0];
    document.getElementById('page-title').textContent = page.label;
    document.querySelectorAll('.nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.id === id);
    });

    clearPageTimers();
    closeModal();

    var content = document.getElementById('content');
    content.innerHTML = '<div class="loading">加载中…</div>';
    PAGES[id]()
      .then(function (html) {
        content.innerHTML = html;
        if (AFTER[id]) AFTER[id](content);
      })
      .catch(function (err) {
        content.innerHTML = errorBox(err);
      });
  }

  function boot() {
    buildNav();

    var tokenInput = document.getElementById('token-input');
    tokenInput.value = state.token;
    document.getElementById('token-save').addEventListener('click', function () {
      state.token = tokenInput.value.trim();
      localStorage.setItem('paperbot_token', state.token);
      toast(state.token ? '令牌已保存' : '已清空令牌', 'ok');
      render();
    });
    document.getElementById('refresh').addEventListener('click', render);

    // 全局重载：重读 .env 配置 + 重载所有插件（POST /reload）
    var reloadAllBtn = document.getElementById('reload-all');
    if (reloadAllBtn) {
      reloadAllBtn.addEventListener('click', function () {
        if (!confirm('重载会重新读取 .env 并重载所有插件，确认？')) return;
        reloadAllBtn.disabled = true;
        send('/reload', 'POST')
          .then(function (res) {
            toast((res && res.message) || '已重载', 'ok');
            var restart = (res && res.config && res.config.restart) || [];
            if (restart.length) {
              toast('以下配置项需重启生效：' + restart.join('、'), 'err');
            }
            var failed = (res && res.plugins && res.plugins.failed) || [];
            if (failed.length) {
              toast(
                '部分插件重载失败：' + failed.map(function (f) { return f.name; }).join('、'),
                'err'
              );
            }
            render();
          })
          .catch(function (err) {
            toast('重载失败：' + err.message, 'err');
          })
          .then(function () {
            reloadAllBtn.disabled = false;
          });
      });
    }

    // 浮窗关闭：× 按钮 + 点遮罩空白处（全局绑一次）
    var overlay = document.getElementById('modal-overlay');
    var closeBtn = document.getElementById('modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (overlay) {
      overlay.addEventListener('click', function (ev) {
        if (ev.target === overlay) closeModal();
      });
    }

    window.addEventListener('hashchange', render);
    if (!location.hash || location.hash === '#/') {
      // 支持直接访问 /status、/config 这类路径（与后端页面路由对应）
      var path = (location.pathname || '/').replace(/^\/+|\/+$/g, '');
      var known = MENU.filter(function (m) {
        return m.id === path;
      })[0];
      location.hash = '#/' + (known ? known.id : MENU[0].id);
    }
    render();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
