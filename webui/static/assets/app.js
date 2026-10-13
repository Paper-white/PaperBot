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

      return (
        '<div class="grid">' +
        stat('运行时长', d.server.uptime_text, '启动于 ' + d.server.started_at) +
        stat('协议端', d.connection.online ? '已连接' : '未连接', bots.map(function (b) { return b.self_id; }).join('、') || '暂无 Bot') +
        stat('插件 / 命令', d.counts.plugins + ' / ' + d.counts.commands, '已注册插件与命令数') +
        stat('群 / 规则 / 授权', d.counts.groups + ' / ' + d.counts.rules + ' / ' + d.counts.grants, '数据库中出现过的群') +
        stat('数据库大小', d.storage.size_text, d.storage.exists ? '文件就绪' : '文件尚未创建') +
        stat('Python / 线程', d.process.python + ' / ' + d.process.threads, d.process.platform + ' · PID ' + d.process.pid) +
        '</div>' +
        '<div class="two-col">' +
        card('协议端连接', botTable, 'OneBot v11 正向 WebSocket') +
        card('消息输出守卫', guardBody, '开发期防误发的安全网') +
        '</div>' +
        '<div class="two-col">' +
        card(
          'WebUI',
          kv([
            ['监听地址', '<span class="mono">' + esc(d.webui.host) + ':' + esc(d.webui.port) + '</span>'],
            ['API 前缀', '<span class="mono">' + esc(d.webui.prefix) + '</span>'],
            ['鉴权', d.webui.token_required ? badge('需 Bearer Token', 'green') : badge('未设置令牌', 'amber')],
          ]),
          '当前页面所在的管理后台'
        ) +
        card(
          '存储',
          kv([
            ['连接串', '<span class="mono">' + esc(d.storage.database) + '</span>'],
            ['大小', esc(d.storage.size_text)],
            ['服务器时间', esc(d.server.now)],
          ]),
          'SQLite（WAL 模式，多插件共享）'
        ) +
        '</div>'
      );
    });
  }

  /* -------------------------------------------------------------- 系统配置 */
  /* 通用字段渲染：系统配置（.env）与插件配置项（数据库）共用。
     opts.noMissing    —— 不显示「未在 .env 中显式设置」提示
     opts.missingHint  —— 未配置时的提示文案
     opts.configuredHint —— 已配置时的提示文案 */
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
    } else if (field.choices && field.choices.length) {
      var current = field.value === null || field.value === undefined ? '' : String(field.value);
      var list = field.choices.slice();
      if (current && list.indexOf(current) < 0) list.unshift(current);
      control =
        '<select class="input wide" ' + common + '>' +
        list
          .map(function (v) {
            return (
              '<option value="' + esc(v) + '"' + (v === current ? ' selected' : '') + '>' + esc(v) + '</option>'
            );
          })
          .join('') +
        '</select>';
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
        field.value === null || field.value === undefined
          ? ''
          : field.type === 'list'
          ? JSON.stringify(field.value)
          : String(field.value);
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

  function configBlock(item, isAdapter) {
    var disabled = item.available === false;
    var note = isAdapter
      ? (disabled ? badge('暂未支持', 'amber') + ' ' : badge('可用', 'green') + ' ') + esc(item.note || '')
      : esc(item.note || '');
    return (
      '<div class="card" data-config-block>' +
      '<div class="card-head"><div>' +
      '<div class="card-title">' + esc(item.name) + '</div>' +
      (note ? '<div class="card-note">' + note + '</div>' : '') +
      '</div><button class="btn small" data-config-save>保存本节</button></div>' +
      '<div class="form-grid">' + item.fields.map(fieldInput).join('') + '</div>' +
      '</div>'
    );
  }

  function pageConfig() {
    return api('/config').then(function (d) {
      var adapters = d.adapters
        .map(function (a) {
          return configBlock(a, true);
        })
        .join('');
      var sections = d.sections
        .map(function (s) {
          return configBlock(s, false);
        })
        .join('');
      return (
        '<div class="error-box warn">' +
        '配置改动会写入 <span class="mono">' + esc(d.path) + '</span>（自动备份 .env.bak），' +
        '<b>需要重启 Bot 才会生效</b>。敏感字段不回显，留空即不修改。' +
        '</div>' +
        card('协议端', adapters) +
        sections
      );
    });
  }

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

  function afterConfig(root) {
    bindConfigSaves(root);
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

  function pagePermissions() {
    return Promise.all([api('/permissions'), api('/groups')]).then(function (res) {
      var d = res[0];
      var groups = res[1];

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
            '<div class="field"><label>群号</label><input class="input short" data-admin-gid placeholder="群号"></div>' +
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

  function pagePlugins() {
    return Promise.all([api('/plugins'), api('/groups')]).then(function (res) {
      var list = res[0];
      var groups = res[1];
      return Promise.all(
        list.items.map(function (p) {
          return api('/plugins/' + encodeURIComponent(p.name)).then(function (detail) {
            if (!detail.config_schema || !detail.config_schema.length) return detail;
            return api('/plugins/' + encodeURIComponent(p.name) + '/config').then(function (cfg) {
              detail.config = cfg;
              return detail;
            });
          });
        })
      ).then(function (details) {
        var groupOptions = groups.items
          .map(function (g) {
            return '<option value="' + esc(g.group_id) + '">' + esc(g.group_name || g.group_id) + '</option>';
          })
          .join('');

        var cards = details
          .map(function (p) {
            var locked = p.protected;
            var toggle =
              '<label class="switch" title="' + (locked ? '受保护插件不可关闭' : '全局开关') + '">' +
              '<input type="checkbox" data-global-toggle="' + esc(p.name) + '"' +
              (p.global_enabled ? ' checked' : '') + (locked ? ' disabled' : '') +
              '><span class="slider"></span></label>';
            var reloadBtn = p.reloadable
              ? '<button class="btn small" data-plugin-reload="' + esc(p.name) + '" ' +
                'title="重新 import 插件模块（改完插件代码不用重启 Bot）">热重载</button>'
              : '';
            var headActions =
              '<div class="card-head-actions">' + reloadBtn + toggle + '</div>';

            var rules = p.rules.length
              ? '<div class="table-wrap"><table><thead><tr>' +
                '<th>作用域</th><th>状态</th><th>修改者</th><th>时间</th><th>操作</th>' +
                '</tr></thead><tbody>' +
                p.rules
                  .map(function (r) {
                    return ruleRow(p.name, r, locked);
                  })
                  .join('') +
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
            var configCard = !configFields.length
              ? ''
              : '<div class="card" data-plugin-config-block="' + esc(p.name) + '">' +
                '<div class="card-head"><div>' +
                '<div class="card-title">插件配置项</div>' +
                '<div class="card-note">存放在 SQLite（表 <span class="mono">plugin_config</span>），' +
                '改动<b>即时生效</b>，无需重启；留空即回落到默认值。</div>' +
                '</div>' +
                '<div class="card-head-actions">' +
                '<button class="btn small" data-plugin-config-clear="' + esc(p.name) + '">恢复默认</button>' +
                '<button class="btn small" data-plugin-config-save="' + esc(p.name) + '">保存配置</button>' +
                '</div></div>' +
                '<div class="form-grid">' +
                configFields
                  .map(function (f) {
                    return fieldInput(f, {
                      noMissing: true,
                      missingHint: '使用默认值',
                      configuredHint: '已自定义',
                    });
                  })
                  .join('') +
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

            return (
              '<div class="card">' +
              '<div class="card-head"><div>' +
              '<div class="card-title">' + esc(p.display_name || p.name) +
              ' <span class="mono dim">' + esc(p.name) + '</span> ' +
              badge('v' + p.version, 'grey') +
              (locked ? ' ' + badge('受保护', 'pink') : '') +
              (!p.default_enabled ? ' ' + badge('默认关闭', 'amber') : '') +
              '</div>' +
              '<div class="card-note">' + esc(p.description || '暂无描述') +
              ' · 当前全局：' + (p.global_enabled ? badge('运行中', 'green') : badge('已停用', 'red')) +
              (p.author ? ' · 作者 ' + esc(p.author) : '') +
              (p.repo ? ' · <a href="' + esc(p.repo) + '" target="_blank" rel="noreferrer">仓库</a>' : '') +
              (p.requires ? ' · 要求 <span class="mono">' + esc(p.requires) + '</span>' : '') +
              '</div>' +
              '</div>' + headActions + '</div>' +
              '<div class="two-col">' +
              '<div><div class="card-note" style="margin-bottom:6px"><b>门控规则</b></div>' + rules + addForm + '</div>' +
              '<div><div class="card-note" style="margin-bottom:6px"><b>注册命令</b></div>' + commands + '</div>' +
              '</div>' + configCard + '</div>'
            );
          })
          .join('');

        return (
          '<div class="error-box warn">插件开关与规则和 QQ 命令（%on / %off）共用同一套数据，改完立即生效（无需重启）。' +
          '受保护插件（如总控插件）不允许通过 WebUI 关闭或热重载。</div>' +
          (cards || empty('没有加载任何插件'))
        );
      });
    });
  }

  function afterPlugins(root) {
    root.querySelectorAll('[data-global-toggle]').forEach(function (box) {
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

    root.querySelectorAll('[data-rule-scope]').forEach(function (select) {
      select.addEventListener('change', function () {
        var name = select.dataset.ruleScope;
        var wrap = root.querySelector('[data-rule-id-wrap="' + name + '"]');
        wrap.style.display = select.value === 'global' ? 'none' : '';
      });
    });

    root.querySelectorAll('[data-rule-add]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.ruleAdd;
        var scope = root.querySelector('[data-rule-scope="' + name + '"]').value;
        var scopeId = scope === 'global' ? '' : root.querySelector('[data-rule-id="' + name + '"]').value.trim();
        var enabled = root.querySelector('[data-rule-enabled="' + name + '"]').value === 'true';
        if (scope !== 'global' && !scopeId) return toast('请填写群号 / QQ', 'err');
        send('/plugins/' + encodeURIComponent(name) + '/rule', 'PUT', {
          scope_type: scope,
          scope_id: scopeId,
          enabled: enabled,
        })
          .then(function () {
            toast('规则已写入', 'ok');
            render();
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
          });
      });
    });

    root.querySelectorAll('[data-rule-del]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.ruleDel;
        var query =
          '?scope_type=' + encodeURIComponent(btn.dataset.scopeType) +
          '&scope_id=' + encodeURIComponent(btn.dataset.scopeId);
        send('/plugins/' + encodeURIComponent(name) + '/rule' + query, 'DELETE')
          .then(function () {
            toast('规则已删除', 'ok');
            render();
          })
          .catch(function (err) {
            toast('删除失败：' + err.message, 'err');
          });
      });
    });

    // 插件配置项：保存
    root.querySelectorAll('[data-plugin-config-save]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.pluginConfigSave;
        var block = root.querySelector('[data-plugin-config-block="' + name + '"]');
        btn.disabled = true;
        send('/plugins/' + encodeURIComponent(name) + '/config', 'PUT', {
          values: collectConfig(block),
        })
          .then(function (res) {
            var changed = res.changed || [];
            toast(changed.length ? '已保存：' + changed.join('、') : '没有需要修改的配置项', 'ok');
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

    // 插件配置项：一键恢复默认（删除该插件的全部配置记录）
    root.querySelectorAll('[data-plugin-config-clear]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.pluginConfigClear;
        btn.disabled = true;
        send('/plugins/' + encodeURIComponent(name) + '/config', 'DELETE')
          .then(function (res) {
            toast('已恢复默认（清除 ' + res.removed + ' 项）', 'ok');
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

    // 热重载
    root.querySelectorAll('[data-plugin-reload]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.pluginReload;
        btn.disabled = true;
        send('/plugins/' + encodeURIComponent(name) + '/reload', 'POST')
          .then(function (res) {
            toast(
              '已热重载 ' + name + '（销毁 ' + res.removed_matchers + ' 个 matcher，v' + res.version + '）',
              'ok'
            );
            render();
          })
          .catch(function (err) {
            toast('热重载失败：' + err.message, 'err');
          })
          .then(function () {
            btn.disabled = false;
          });
      });
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

  function pageCommands() {
    return api('/commands').then(function (d) {
      var levels = d.levels || [];
      var cards = d.items.length
        ? d.items
            .map(function (p) {
              var rows = p.commands.length
                ? p.commands
                    .map(function (c) {
                      c.plugin = p.plugin;
                      var levelCell = levelSelect(c, levels);
                      var tip = c.overridden
                        ? '已改过（默认 ' + esc(c.default_level_label) + '）'
                        : '默认';
                      return (
                        '<tr><td class="mono">' + esc(c.display) + '</td>' +
                        '<td class="mono hint">' + esc(c.alias_display || '-') + '</td>' +
                        '<td>' + esc(c.description || '-') + '</td>' +
                        '<td>' + levelCell + '<div class="hint">' + tip + '</div></td></tr>'
                      );
                    })
                    .join('')
                : '<tr><td colspan="4" class="hint">该插件没有注册命令</td></tr>';
              return (
                '<div class="card"><div class="card-head"><div>' +
                '<div class="card-title">' + esc(p.display_name || p.plugin) +
                ' <span class="mono dim">' + esc(p.plugin) + '</span> ' + badge('v' + p.version, 'grey') +
                (p.protected ? ' ' + badge('受保护', 'pink') : '') + '</div>' +
                '<div class="card-note">' + esc(p.description || '暂无描述') + ' · ' + p.command_count + ' 条命令</div>' +
                '</div></div>' +
                '<div class="table-wrap"><table><thead><tr>' +
                '<th>命令</th><th>别名</th><th>功能介绍</th><th>所需权限</th>' +
                '</tr></thead><tbody>' + rows + '</tbody></table></div></div>'
              );
            })
            .join('')
        : empty('没有已注册的命令');

      return (
        '<div class="error-box warn">群里发命令时要带前缀 <span class="mono">' + esc(d.prefix || '（未设置）') +
        '</span>，例如 <span class="mono">' + esc(d.prefix) + 'help</span>。共 ' +
        d.plugin_count + ' 个插件 / ' + d.command_count + ' 条命令。</div>' +
        '<div class="hint spaced">命令的<b>默认权限</b>由插件代码声明；这里的<b>实际权限</b>由框架维护，' +
        '改动写入数据库并<b>立即生效</b>。当前有 ' + (d.override_count || 0) + ' 条被改过。' +
        '受保护插件（总控台）与「插件自管权限」的命令不可改。</div>' + cards
      );
    });
  }

  function afterCommands(root) {
    root.querySelectorAll('[data-cmd-level]').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var level = parseInt(sel.value, 10);
        sel.disabled = true;
        send(
          '/commands/' +
            encodeURIComponent(sel.dataset.plugin) + '/' +
            encodeURIComponent(sel.dataset.command),
          'PUT',
          { level: level }
        )
          .then(function (res) {
            toast(res.message || '已更新', 'ok');
            render();
          })
          .catch(function (err) {
            toast('更新失败：' + err.message, 'err');
            sel.disabled = false;
            render();
          });
      });
    });

    root.querySelectorAll('[data-cmd-reset]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        btn.disabled = true;
        send(
          '/commands/' +
            encodeURIComponent(btn.dataset.plugin) + '/' +
            encodeURIComponent(btn.dataset.command),
          'DELETE'
        )
          .then(function (res) {
            toast(res.message || '已恢复默认', 'ok');
            render();
          })
          .catch(function (err) {
            toast('操作失败：' + err.message, 'err');
            btn.disabled = false;
          });
      });
    });
  }

  /* ---------------------------------------------------------------- 设置页 */
  function pageSettings() {
    return api('/settings').then(function (d) {
      var v = d.version;
      var r = d.runtime;
      var logs = d.logs;
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

      var logRows = logs.files.length
        ? '<div class="table-wrap"><table><thead><tr>' +
          '<th>日志文件</th><th>大小</th><th>最后写入</th><th>操作</th>' +
          '</tr></thead><tbody>' +
          logs.files
            .map(function (f) {
              return (
                '<tr><td class="mono">' + esc(f.name) +
                (f.current ? ' ' + badge('当前', 'green') : '') + '</td>' +
                '<td>' + esc(f.size_text) + '</td>' +
                '<td class="hint">' + esc(f.modified_at) + '</td>' +
                '<td><button class="btn ghost small" data-log-view="' + esc(f.name) + '">查看</button>' +
                (f.current
                  ? ''
                  : ' <button class="btn ghost small" data-log-del="' + esc(f.name) + '">删除</button>') +
                '</td></tr>'
              );
            })
            .join('') +
          '</tbody></table></div>'
        : empty(logs.enabled ? '还没有日志文件（重启 Bot 后生成）' : '日志文件输出已关闭（LOG_FILE_ENABLE=false）');

      var logCard = card(
        '日志文件',
        '<div class="form-row">' +
          '<div class="field"><label>目录</label><div class="mono">' + esc(logs.dir) + '</div></div>' +
          '<div class="field"><label>级别</label><div>' + badge(logs.level, 'blue') + '</div></div>' +
          '<div class="field"><label>保留</label><div>' + badge(logs.keep_days + ' 天', 'grey') + '</div></div>' +
          '<div class="field"><label>占用</label><div>' + esc(logs.total_text) + ' · ' +
          logs.files.length + ' 个文件</div></div>' +
          '<button class="btn danger" id="clear-logs">清理全部日志</button>' +
        '</div>' +
        '<div class="spaced">' + logRows + '</div>' +
        '<div id="log-view"></div>',
        logs.enabled ? '本次运行：' + esc(logs.current) : '未启用'
      );

      // 从「系统配置」迁过来的可编辑分组：服务与后台 / 日志
      var sectionCards = (d.sections || [])
        .map(function (s) {
          return configBlock(s, false);
        })
        .join('');
      var sectionBox = sectionCards
        ? '<div class="error-box warn">下面这些配置会写入 <span class="mono">' +
          esc(d.config_path || '.env') +
          '</span>（自动备份 .env.bak），<b>需要重启 Bot 才会生效</b>；' +
          'WebUI 令牌不在这里改，见上方卡片（可即时生效）。</div>' + sectionCards
        : '';

      return versionCard + runtimeCard + sectionBox + tokenCard + logCard;
    });
  }

  function afterSettings(root) {
    bindConfigSaves(root);

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

    var clearBtn = root.querySelector('#clear-logs');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        if (!window.confirm('确定清理全部日志吗？（当前运行的日志会被清空）')) return;
        send('/settings/logs', 'DELETE')
          .then(function (res) {
            toast('已清理 ' + res.deleted + ' 个文件，释放 ' + res.freed_text, 'ok');
            render();
          })
          .catch(function (err) {
            toast(err.message || '清理失败', 'err');
          });
      });
    }

    root.querySelectorAll('[data-log-view]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var box = root.querySelector('#log-view');
        box.innerHTML = '<div class="loading">读取中…</div>';
        api('/settings/logs/' + encodeURIComponent(btn.dataset.logView) + '?lines=200')
          .then(function (res) {
            box.innerHTML = '<div class="log-view">' + esc(res.content || '（空文件）') + '</div>';
          })
          .catch(function (err) {
            box.innerHTML = errorBox(err);
          });
      });
    });

    root.querySelectorAll('[data-log-del]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.logDel;
        if (!window.confirm('删除日志文件 ' + name + ' ？')) return;
        send('/settings/logs/' + encodeURIComponent(name), 'DELETE')
          .then(function (res) {
            toast('已删除，释放 ' + res.freed_text, 'ok');
            render();
          })
          .catch(function (err) {
            toast(err.message || '删除失败', 'err');
          });
      });
    });
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
    ]).then(function (res) {
      var st = res[0];
      var prov = res[1];
      var presets = res[2];
      var sessions = res[3];
      var users = res[4];

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
        stat('流式 / 记忆', (st.stream_enable ? '开' : '关') + ' / ' + (st.memory_enable ? '开' : '关'), '上下文 ' + st.context_rounds + ' 轮 · 每 ' + st.impression_every + ' 条提炼印象') +
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
                '<td>' + (p.api_key_set ? badge('已设置', 'green') : badge('未设置', 'amber')) + '</td>' +
                '<td>' + actions + '</td></tr>'
              );
            })
            .join('')
        : '<tr><td colspan="6" class="hint">还没有服务商（当前只能靠 .env 兜底）</td></tr>';

      var provCard = card(
        '服务商',
        '<div class="table-wrap"><table><thead><tr>' +
          '<th>名称</th><th>状态</th><th>API 地址</th><th>模型</th><th>密钥</th><th>操作</th>' +
          '</tr></thead><tbody>' + provRows + '</tbody></table></div>' +
          '<div class="form-row spaced">' +
          '<div class="field"><label>名称</label><input class="input" id="llm-prov-name" placeholder="如 deepseek"></div>' +
          '<div class="field" style="flex:1;min-width:240px"><label>API 地址</label>' +
          '<input class="input wide" id="llm-prov-url" placeholder="https://api.deepseek.com/v1"></div>' +
          '<div class="field" style="flex:1;min-width:200px"><label>API Key</label>' +
          '<input class="input wide" id="llm-prov-key" type="password" placeholder="留空 = 不改（新建时为空）" autocomplete="off"></div>' +
          '<div class="field"><label>模型</label><input class="input" id="llm-prov-model" placeholder="deepseek-chat"></div>' +
          '<div class="field"><label>温度</label><input class="input short" id="llm-prov-temp" placeholder="留空默认"></div>' +
          '<div class="field"><label>设为默认</label><select class="input" id="llm-prov-default">' +
          '<option value="1">是（推荐）</option><option value="0">否</option></select></div>' +
          '<button class="btn" data-llm-prov-save>保存服务商</button>' +
          '</div>' +
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

      var html = overview + provCard + chatCard + presetCard + sessionCard + userCard;
      return html;

    });
  }

  function fillProviderForm(root, p) {
    root.querySelector('#llm-prov-name').value = p.name || '';
    root.querySelector('#llm-prov-url').value = p.base_url || '';
    root.querySelector('#llm-prov-key').value = p.api_key_set ? (p.api_key || '') : '';
    root.querySelector('#llm-prov-model').value = p.model || '';
    root.querySelector('#llm-prov-temp').value = p.temperature === null || p.temperature === undefined ? '' : p.temperature;
    root.querySelector('#llm-prov-default').value = p.is_default ? '1' : '0';
    toast('已载入「' + (p.name || '') + '」，密钥留空即保持原值', 'ok');
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
          model: val('#llm-prov-model').trim(),
          is_default: val('#llm-prov-default') === '1',
        };
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
    settings: pageSettings,
  };

  var AFTER = {
    config: afterConfig,
    permissions: afterPermissions,
    plugins: afterPlugins,
    llm: afterLlm,
    database: afterDatabase,
    commands: afterCommands,
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

  function render() {
    var id = currentPageId();
    var page = MENU.filter(function (m) {
      return m.id === id;
    })[0];
    document.getElementById('page-title').textContent = page.label;
    document.querySelectorAll('.nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.id === id);
    });

    var content = document.getElementById('content');
    content.innerHTML = '<div class="loading">加载中…</div>';
    PAGES[id]()
      .then(function (html) {
        content.innerHTML = html;
        if (AFTER[id]) AFTER[id](content);
        updateConn();
      })
      .catch(function (err) {
        content.innerHTML = errorBox(err);
      });
  }

  function updateConn() {
    var dot = document.getElementById('conn-dot');
    var text = document.getElementById('conn-text');
    api('/status')
      .then(function (d) {
        var online = d.connection.online;
        dot.className = 'dot ' + (online ? 'on' : 'off');
        text.textContent = online ? '协议端已连接' : '协议端未连接';
      })
      .catch(function () {
        dot.className = 'dot off';
        text.textContent = '无法访问后端';
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
    setInterval(updateConn, 20000);
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
