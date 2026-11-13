/* UI Prompt Manager: список промтов, дерево папок, редакторы поверх window.pywebview.api. */
(function () {
  'use strict';

  const STORAGE = {
    collapsed: 'pm.sidebar.collapsed',
    expanded: 'pm.tree.expanded',
    folder: 'pm.folder',
    sort: 'pm.sort',
  };

  const dom = {
    sidebar: document.getElementById('sidebar'),
    folderTree: document.getElementById('folder-tree'),
    breadcrumbs: document.getElementById('breadcrumbs'),
    folderCount: document.getElementById('folder-count'),
    searchInput: document.getElementById('search-input'),
    searchClear: document.getElementById('search-clear'),
    tagCloudBox: document.getElementById('tag-cloud-box'),
    tagCloud: document.getElementById('tag-cloud'),
    tagsClear: document.getElementById('tags-clear'),
    sortSelect: document.getElementById('sort-select'),
    resetFilters: document.getElementById('btn-reset-filters'),
    emptyReset: document.getElementById('btn-empty-reset'),
    list: document.getElementById('list'),
    empty: document.getElementById('empty'),
    emptyTitle: document.getElementById('empty-title'),
    emptyText: document.getElementById('empty-text'),
    emptyIndex: document.getElementById('empty-index'),
    loading: document.getElementById('loading'),
    stats: document.getElementById('stats'),
    status: document.getElementById('status'),
    dataRoot: document.getElementById('data-root'),
    version: document.getElementById('version'),
    banner: document.getElementById('banner'),
    bannerTitle: document.getElementById('banner-title'),
    bannerText: document.getElementById('banner-text'),
    bannerList: document.getElementById('banner-list'),
    bannerClose: document.getElementById('banner-close'),
    btnReload: document.getElementById('btn-reload'),
    btnNew: document.getElementById('btn-new-prompt'),
    btnSync: document.getElementById('btn-sync'),
    btnTestDav: document.getElementById('btn-test-dav'),
    btnEmptyNew: document.getElementById('btn-empty-new'),
    btnSidebar: document.getElementById('btn-sidebar'),
    btnCollapse: document.getElementById('btn-collapse'),
    btnExpand: document.getElementById('btn-expand'),
    modalRoot: document.getElementById('modal-root'),
    modalTitle: document.getElementById('modal-title'),
    modalMessage: document.getElementById('modal-message'),
    modalFields: document.getElementById('modal-fields'),
    modalForm: document.getElementById('modal-form'),
    modalError: document.getElementById('modal-error'),
    modalSubmit: document.getElementById('modal-submit'),
    modalCancel: document.getElementById('modal-cancel'),
  };

  const state = {
    booted: false,
    busy: false,
    syncing: false,
    prompts: [],
    folders: [],
    folderPaths: [],
    tagsCloud: [],
    query: '',
    selectedTags: new Set(),
    sort: parseSort(safeStorageGet(STORAGE.sort)).sort,
    order: parseSort(safeStorageGet(STORAGE.sort)).order,
    stats: { prompts: 0, variants: 0, tags: 0, folders: 0, filtered: 0 },
    selectedFolder: safeStorageGet(STORAGE.folder) || '',
    expanded: loadExpanded(),
    sidebarCollapsed: safeStorageGet(STORAGE.collapsed) === '1',
    modal: null,
    menu: null,
    menuHandler: null,
    imageCache: new Map(),
  };
  let searchTimer = null;

  const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  /* --- утилиты -------------------------------------------------------- */

  function safeStorageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function safeStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      /* приватный режим WebView2 — просто не запоминаем состояние */
    }
  }

  function parseSort(raw) {
    const match = /^(updated|created|title)-(asc|desc)$/.exec(String(raw || ''));
    if (!match) {
      return { sort: 'updated', order: 'desc' };
    }
    return { sort: match[1], order: match[2] };
  }

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function queryTokens() {
    return String(state.query || '')
      .split(/\s+/)
      .filter(Boolean);
  }

  function appendHighlighted(container, text, tokens) {
    if (!tokens.length) {
      container.textContent = text;
      return;
    }
    const pattern = new RegExp('(' + tokens.map(escapeRegExp).join('|') + ')', 'gi');
    const parts = text.split(pattern);
    parts.forEach(function (part, index) {
      if (!part) {
        return;
      }
      if (index % 2 === 1) {
        const mark = document.createElement('mark');
        mark.textContent = part;
        container.appendChild(mark);
      } else {
        container.appendChild(document.createTextNode(part));
      }
    });
  }

  function plural(count, forms) {
    const abs = Math.abs(count) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) {
      return forms[2];
    }
    if (last > 1 && last < 5) {
      return forms[1];
    }
    if (last === 1) {
      return forms[0];
    }
    return forms[2];
  }

  function countLabel(count, forms) {
    return count + ' ' + plural(count, forms);
  }

  function splitTags(raw) {
    return String(raw || '')
      .split(',')
      .map(function (item) {
        return item.trim();
      })
      .filter(Boolean);
  }

  function formatDate(iso) {
    if (!iso) {
      return '—';
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return iso;
    }
    return dateFormatter.format(date);
  }

  function loadExpanded() {
    const raw = safeStorageGet(STORAGE.expanded);
    if (raw === null) {
      return null;
    }
    try {
      const parsed = JSON.parse(raw);
      return new Set(Array.isArray(parsed) ? parsed.map(String) : []);
    } catch (error) {
      return new Set();
    }
  }

  function collectPaths(nodes, target) {
    (nodes || []).forEach(function (node) {
      target.add(node.path);
      collectPaths(node.children, target);
    });
    return target;
  }

  /* --- мост pywebview -------------------------------------------------- */

  class ApiError extends Error {
    constructor(message, code) {
      super(message);
      this.name = 'ApiError';
      this.code = code || 'error';
    }
  }

  function apiObject(method) {
    const api = window.pywebview && window.pywebview.api;
    if (!api) {
      return null;
    }
    if (method && typeof api[method] !== 'function') {
      return null;
    }
    return api;
  }

  async function call(method, ...args) {
    const api = apiObject(method);
    if (!api) {
      throw new ApiError('Мост pywebview недоступен. Запустите приложение командой python main.py.', 'no_bridge');
    }
    let response;
    try {
      response = await api[method](...args);
    } catch (error) {
      const detail = error && error.message ? error.message : String(error);
      throw new ApiError('Вызов «' + method + '» не выполнен: ' + detail, 'bridge_error');
    }
    if (!response || typeof response !== 'object') {
      throw new ApiError('Бэкенд вернул некорректный ответ.', 'bad_response');
    }
    if (response.ok !== true) {
      const info = response.error || {};
      throw new ApiError(info.message || 'Неизвестная ошибка', info.code);
    }
    return response.data;
  }

  /* --- общий каркас ---------------------------------------------------- */

  function setStatus(text) {
    dom.status.textContent = text;
  }

  function setBusy(busy) {
    dom.loading.hidden = !busy || state.prompts.length > 0;
    dom.btnReload.disabled = busy;
    dom.btnNew.disabled = busy;
  }

  function showBanner(title, message, details, kind) {
    dom.bannerTitle.textContent = title;
    dom.bannerText.textContent = message || '';
    dom.bannerList.textContent = '';
    dom.banner.classList.toggle('info', kind === 'info');
    (details || []).slice(0, 8).forEach(function (line) {
      const item = document.createElement('li');
      item.textContent = line;
      dom.bannerList.appendChild(item);
    });
    dom.banner.hidden = false;
  }

  function hideBanner() {
    dom.banner.hidden = true;
    dom.bannerList.textContent = '';
    dom.banner.classList.remove('info');
  }

  async function refresh() {
    if (state.busy) {
      return;
    }
    state.busy = true;
    setBusy(true);
    setStatus('Чтение index.json…');
    try {
      const data = await call(
        'get_state',
        state.query,
        state.selectedFolder,
        Array.from(state.selectedTags),
        state.sort,
        state.order
      );
      renderState(data);
      hideBanner();
      setStatus('Готово');
    } catch (error) {
      state.prompts = [];
      renderEmpty();
      showBanner('Не удалось загрузить данные', error.message, []);
      setStatus('Ошибка');
    } finally {
      state.busy = false;
      setBusy(false);
    }
  }

  function renderState(data) {
    state.prompts = Array.isArray(data.prompts) ? data.prompts : [];
    state.folders = Array.isArray(data.folders) ? data.folders : [];
    state.folderPaths = Array.isArray(data.folder_paths) ? data.folder_paths : [];
    state.tagsCloud = Array.isArray(data.tags_cloud) ? data.tags_cloud : [];
    state.stats = data.stats || {
      prompts: 0,
      variants: 0,
      tags: 0,
      folders: 0,
      filtered: 0,
    };

    if (state.expanded === null) {
      state.expanded = collectPaths(state.folders, new Set());
      safeStorageSet(STORAGE.expanded, JSON.stringify(Array.from(state.expanded)));
    }

    if (state.selectedFolder && !state.folderPaths.includes(state.selectedFolder)) {
      state.selectedFolder = '';
      safeStorageSet(STORAGE.folder, '');
    }

    const knownTags = new Set(state.tagsCloud.map(function (item) {
      return item.tag;
    }));
    Array.from(state.selectedTags).forEach(function (tag) {
      if (!knownTags.has(tag)) {
        state.selectedTags.delete(tag);
      }
    });

    const app = data.app || {};
    dom.dataRoot.textContent = app.data_root ? 'Данные: ' + app.data_root : '';
    dom.dataRoot.title = app.prompts_dir || '';
    dom.emptyIndex.textContent = app.index_path || 'prompts/index.json';
    dom.version.textContent = app.version ? 'v' + app.version : '';
    dom.stats.textContent =
      countLabel(state.stats.prompts, ['промт', 'промта', 'промтов']) +
      ' · ' +
      countLabel(state.stats.variants, ['вариант', 'варианта', 'вариантов']) +
      ' · ' +
      countLabel(state.stats.folders, ['папка', 'папки', 'папок']);

    renderSearchControls();
    renderTagCloud();
    renderSortControls();
    renderTree();
    renderBreadcrumbs();
    renderList();

    if (Array.isArray(data.repairs) && data.repairs.length) {
      showBanner(
        'Индекс пришлось исправить',
        'При чтении index.json обнаружены расхождения с файлами — данные сохранены, подробности ниже.',
        data.repairs
      );
    }
  }

  /* --- поиск, теги, сортировка ----------------------------------------- */

  function renderSearchControls() {
    if (dom.searchInput.value !== state.query) {
      dom.searchInput.value = state.query;
    }
    dom.searchClear.hidden = !state.query;
  }

  function renderTagCloud() {
    dom.tagCloud.textContent = '';
    dom.tagCloudBox.hidden = state.tagsCloud.length === 0;
    dom.tagsClear.hidden = state.selectedTags.size === 0;

    if (!state.tagsCloud.length) {
      return;
    }

    const max = state.tagsCloud.reduce(function (acc, item) {
      return Math.max(acc, item.count);
    }, 1);

    state.tagsCloud.forEach(function (item) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'tag-chip' + (state.selectedTags.has(item.tag) ? ' active' : '');
      chip.title = 'Промтов с тегом «' + item.tag + '»: ' + item.count;
      chip.style.fontSize = 11.5 + 3 * (item.count / max) + 'px';

      const label = document.createElement('span');
      label.textContent = '#' + item.tag;

      const count = document.createElement('span');
      count.className = 'tag-count';
      count.textContent = String(item.count);

      chip.append(label, count);
      chip.addEventListener('click', function () {
        toggleTag(item.tag);
      });
      dom.tagCloud.appendChild(chip);
    });
  }

  function renderSortControls() {
    const value = state.sort + '-' + state.order;
    if (dom.sortSelect.value !== value) {
      dom.sortSelect.value = value;
    }
    const hasFilters = Boolean(state.query) || state.selectedTags.size > 0 || Boolean(state.selectedFolder);
    dom.resetFilters.hidden = !hasFilters;
    dom.emptyReset.hidden = !hasFilters;
  }

  function toggleTag(tag) {
    if (state.selectedTags.has(tag)) {
      state.selectedTags.delete(tag);
    } else {
      state.selectedTags.add(tag);
    }
    renderTagCloud();
    renderSortControls();
    refresh();
  }

  function clearSearch() {
    if (!state.query) {
      return;
    }
    state.query = '';
    renderSearchControls();
    renderSortControls();
    refresh();
  }

  function resetFilters() {
    state.query = '';
    state.selectedTags.clear();
    state.selectedFolder = '';
    safeStorageSet(STORAGE.folder, '');
    renderSearchControls();
    renderTagCloud();
    renderSortControls();
    renderTree();
    renderBreadcrumbs();
    refresh();
  }

  /* --- синхронизация --------------------------------------------------- */

  async function runSync() {
    if (state.syncing) {
      return;
    }
    state.syncing = true;
    dom.btnSync.disabled = true;
    dom.btnTestDav.disabled = true;
    setStatus('Синхронизация…');
    try {
      const report = await call('sync');
      state.imageCache.clear();
      await refresh();

      const details = [];
      if (Array.isArray(report.conflicts) && report.conflicts.length) {
        details.push('Конфликты: ' + report.conflicts.join(', '));
      }
      (report.notes || []).forEach(function (note) {
        details.push(note);
      });
      (report.errors || []).forEach(function (error) {
        details.push('Ошибка: ' + error);
      });

      showBanner('Синхронизация завершена', report.summary || 'Изменений нет', details, 'info');
      setStatus(report.summary || 'Синхронизация завершена');
    } catch (error) {
      showBanner('Синхронизация не выполнена', error.message, []);
      setStatus('Ошибка синхронизации');
    } finally {
      state.syncing = false;
      dom.btnSync.disabled = false;
      dom.btnTestDav.disabled = false;
    }
  }

  async function testDavConnection() {
    setStatus('Проверка WebDAV…');
    try {
      const info = await call('test_connection');
      showBanner(
        'WebDAV доступен',
        'Каталог: ' + info.url + ' · объектов в корне: ' + info.entries,
        [],
        'info'
      );
      setStatus('WebDAV доступен');
    } catch (error) {
      showBanner('WebDAV недоступен', error.message, []);
      setStatus('Ошибка WebDAV');
    }
  }

  window.pmSyncProgress = function (message) {
    setStatus(String(message || 'Синхронизация…'));
  };

  /* --- дерево папок ---------------------------------------------------- */

  function renderTree() {
    dom.folderTree.textContent = '';
    dom.folderTree.appendChild(rootTreeRow());
    state.folders.forEach(function (node) {
      dom.folderTree.appendChild(treeBranch(node, 1));
    });
  }

  function rootTreeRow() {
    const row = document.createElement('div');
    row.className = 'tree-row';
    if (!state.selectedFolder) {
      row.classList.add('selected');
    }
    row.setAttribute('role', 'treeitem');
    row.tabIndex = 0;

    const spacer = document.createElement('span');
    spacer.className = 'tree-toggle placeholder';
    spacer.textContent = '';

    const name = document.createElement('span');
    name.className = 'tree-name';
    name.textContent = 'Все промты';

    const badge = document.createElement('span');
    badge.className = 'tree-badge';
    badge.textContent = String(state.stats.prompts);

    row.append(spacer, name, badge);
    row.addEventListener('click', function () {
      selectFolder('');
    });
    row.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        selectFolder('');
      }
    });
    return row;
  }

  function treeBranch(node, depth) {
    const branch = document.createElement('div');
    branch.className = 'tree-branch';

    const row = document.createElement('div');
    row.className = 'tree-row';
    row.dataset.path = node.path;
    row.setAttribute('role', 'treeitem');
    row.tabIndex = 0;
    row.style.paddingLeft = 8 + depth * 14 + 'px';
    if (state.selectedFolder === node.path) {
      row.classList.add('selected');
    }

    const children = node.children || [];
    const isExpanded = state.expanded.has(node.path);

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'tree-toggle' + (children.length ? '' : ' placeholder');
    toggle.tabIndex = -1;
    if (children.length) {
      toggle.appendChild(makeIcon(isExpanded ? 'fa-chevron-down' : 'fa-chevron-right'));
      toggle.title = isExpanded ? 'Свернуть папку' : 'Развернуть папку';
      toggle.addEventListener('click', function (event) {
        event.stopPropagation();
        toggleExpanded(node.path);
      });
    }

    const name = document.createElement('span');
    name.className = 'tree-name';
    name.textContent = node.name;
    name.title = node.path;

    const badge = document.createElement('span');
    badge.className = 'tree-badge';
    badge.textContent = String(node.total);

    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'tree-more';
    more.title = 'Действия с папкой';
    more.setAttribute('aria-label', 'Действия с папкой ' + node.path);
    more.appendChild(makeIcon('fa-ellipsis'));
    more.addEventListener('click', function (event) {
      event.stopPropagation();
      openFolderMenu(node, more);
    });

    row.append(toggle, name, badge, more);
    row.addEventListener('click', function () {
      selectFolder(node.path);
    });
    row.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        selectFolder(node.path);
      }
    });

    branch.appendChild(row);

    if (children.length && isExpanded) {
      const childBox = document.createElement('div');
      childBox.className = 'tree-children';
      children.forEach(function (child) {
        childBox.appendChild(treeBranch(child, depth + 1));
      });
      branch.appendChild(childBox);
    }
    return branch;
  }

  function toggleExpanded(path) {
    if (state.expanded.has(path)) {
      state.expanded.delete(path);
    } else {
      state.expanded.add(path);
    }
    safeStorageSet(STORAGE.expanded, JSON.stringify(Array.from(state.expanded)));
    renderTree();
  }

  function expandPath(folder) {
    if (!folder) {
      return;
    }
    const parts = folder.split('/');
    for (let index = 1; index <= parts.length; index += 1) {
      state.expanded.add(parts.slice(0, index).join('/'));
    }
    safeStorageSet(STORAGE.expanded, JSON.stringify(Array.from(state.expanded)));
  }

  function selectFolder(path) {
    state.selectedFolder = path || '';
    safeStorageSet(STORAGE.folder, state.selectedFolder);
    renderTree();
    renderBreadcrumbs();
    renderList();
  }

  function renderBreadcrumbs() {
    dom.breadcrumbs.textContent = '';

    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'crumb' + (state.selectedFolder ? '' : ' current');
    all.textContent = 'Все промты';
    if (state.selectedFolder) {
      all.addEventListener('click', function () {
        selectFolder('');
      });
    }
    dom.breadcrumbs.appendChild(all);

    if (!state.selectedFolder) {
      return;
    }

    const parts = state.selectedFolder.split('/');
    parts.forEach(function (part, index) {
      const separator = document.createElement('span');
      separator.className = 'crumb-sep';
      separator.textContent = '›';
      dom.breadcrumbs.appendChild(separator);

      const path = parts.slice(0, index + 1).join('/');
      const isLast = index === parts.length - 1;
      const crumb = document.createElement('button');
      crumb.type = 'button';
      crumb.className = 'crumb' + (isLast ? ' current' : '');
      crumb.textContent = part;
      crumb.title = path;
      if (!isLast) {
        crumb.addEventListener('click', function () {
          selectFolder(path);
        });
      }
      dom.breadcrumbs.appendChild(crumb);
    });
  }

  /* --- список промтов -------------------------------------------------- */

  function renderList() {
    const prompts = state.prompts;
    const filtered = state.stats.filtered === undefined ? prompts.length : state.stats.filtered;
    dom.list.textContent = '';
    dom.folderCount.textContent = countLabel(filtered, ['промт', 'промта', 'промтов']);

    if (!prompts.length) {
      dom.list.hidden = true;
      renderEmpty();
      return;
    }

    dom.empty.hidden = true;
    dom.list.hidden = false;

    const fragment = document.createDocumentFragment();
    prompts.forEach(function (prompt, index) {
      fragment.appendChild(buildCard(prompt, index));
    });
    dom.list.appendChild(fragment);
  }

  function renderEmpty() {
    const hasQuery = Boolean(state.query);
    const hasTags = state.selectedTags.size > 0;
    const hasFilters = hasQuery || hasTags || Boolean(state.selectedFolder);

    dom.empty.hidden = false;
    dom.list.hidden = true;
    dom.emptyReset.hidden = !hasFilters;
    dom.btnEmptyNew.hidden = false;

    if (hasQuery || hasTags) {
      const parts = [];
      if (hasQuery) {
        parts.push('запрос «' + state.query + '»');
      }
      if (hasTags) {
        parts.push(
          'теги: ' +
            Array.from(state.selectedTags)
              .map(function (tag) {
                return '#' + tag;
              })
              .join(', ')
        );
      }
      if (state.selectedFolder) {
        parts.push('папка «' + state.selectedFolder + '»');
      }
      dom.emptyTitle.textContent = 'Ничего не найдено';
      dom.emptyText.textContent = 'Нет промтов, подходящих под ' + parts.join(' · ') + '.';
      return;
    }

    if (state.selectedFolder) {
      dom.emptyTitle.textContent = 'В папке «' + state.selectedFolder + '» пусто';
      dom.emptyText.textContent = 'Создайте промт — он сохранится в этой папке.';
      return;
    }

    dom.emptyTitle.textContent = 'Промтов пока нет';
    dom.emptyText.textContent = 'Создайте первый промт: появится вариант и папка.';
  }

  function buildCard(prompt, position) {
    const card = document.createElement('article');
    card.className = 'prompt-card';
    card.dataset.promptId = prompt.id || '';

    const header = document.createElement('header');
    const title = document.createElement('h2');
    title.className = 'prompt-title';
    title.textContent = prompt.title || 'Без названия';
    const meta = document.createElement('span');
    meta.className = 'prompt-meta';
    meta.textContent = '#' + (position + 1) + ' · ' + formatDate(prompt.updated);
    header.append(title, meta);

    const tags = document.createElement('div');
    tags.className = 'tags';
    (prompt.tags || []).forEach(function (tag) {
      const chip = document.createElement('span');
      chip.className = 'tag';
      chip.textContent = '#' + tag;
      tags.appendChild(chip);
    });

    const variants = document.createElement('ul');
    variants.className = 'variants';
    (prompt.variants || []).forEach(function (variant, index) {
      variants.appendChild(buildVariantRow(prompt, variant, index));
    });

    const foot = document.createElement('div');
    foot.className = 'card-foot';

    const folder = document.createElement('span');
    folder.className = 'folder-chip';
    folder.title = prompt.folder || 'Корень';
    folder.append(makeIcon('fa-folder'));
    const folderText = document.createElement('span');
    folderText.className = 'folder-path';
    folderText.textContent = prompt.folder || 'Корень';
    folder.appendChild(folderText);

    const actions = document.createElement('div');
    actions.className = 'card-actions';
    actions.append(
      iconButton('fa-pen', 'Изменить промт', 'Изменить заголовок, теги и папку', function () {
        openPromptDialog(prompt);
      }),
      iconButton('fa-plus', 'Добавить вариант', 'Добавить вариант', function () {
        openVariantDialog(prompt, null);
      }),
      iconButton('fa-trash-can', 'Удалить промт', 'Переместить промт в .trash/', function () {
        confirmDeletePrompt(prompt);
      }, 'danger-text')
    );

    foot.append(folder, actions);
    card.append(header, tags, variants, foot);
    return card;
  }

  function makeIcon(iconClass) {
    const icon = document.createElement('i');
    icon.className = 'fa-solid ' + iconClass;
    icon.setAttribute('aria-hidden', 'true');
    return icon;
  }

  function iconButton(iconClass, label, title, onClick, extraClass) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'icon-btn' + (extraClass ? ' ' + extraClass : '');
    button.title = title;
    button.setAttribute('aria-label', label);
    button.appendChild(makeIcon(iconClass));
    button.addEventListener('click', function (event) {
      Promise.resolve()
        .then(function () {
          return onClick(event);
        })
        .catch(function (error) {
          showBanner('Действие не выполнено', error && error.message ? error.message : String(error), []);
          setStatus('Ошибка');
        });
    });
    return button;
  }

  function cacheImage(key, value) {
    if (state.imageCache.size > 200) {
      const oldest = state.imageCache.keys().next();
      if (!oldest.done) {
        state.imageCache.delete(oldest.value);
      }
    }
    state.imageCache.set(key, value);
  }

  function loadThumb(image) {
    const key = image.dataset.prompt + ':' + image.dataset.variant + ':' + (image.dataset.rev || '');
    if (state.imageCache.has(key)) {
      image.src = state.imageCache.get(key);
      return;
    }
    call('get_variant_image', image.dataset.prompt, image.dataset.variant)
      .then(function (data) {
        if (!data || !data.data_url) {
          return;
        }
        cacheImage(key, data.data_url);
        image.src = data.data_url;
      })
      .catch(function () {
        /* картинка не критична для списка — молча оставляем плейсхолдер */
      });
  }

  function buildVariantRow(prompt, variant, index) {
    const row = document.createElement('li');
    row.className = 'variant';

    const badge = document.createElement('span');
    badge.className = 'variant-index';
    badge.textContent = String(index + 1);

    const main = document.createElement('div');
    main.className = 'variant-main';

    const description = document.createElement('p');
    description.className = 'variant-description';
    if (variant.description) {
      description.textContent = variant.description;
    } else {
      description.classList.add('is-empty');
      description.textContent = 'без описания';
    }
    main.appendChild(description);

    if (variant.snippet) {
      const snippet = document.createElement('p');
      snippet.className = 'variant-snippet';
      appendHighlighted(snippet, variant.snippet, queryTokens());
      main.appendChild(snippet);
    } else if (variant.preview) {
      const preview = document.createElement('p');
      preview.className = 'variant-preview';
      preview.textContent = variant.preview;
      main.appendChild(preview);
    }

    const actions = document.createElement('div');
    actions.className = 'variant-actions';
    actions.append(
      iconButton(
        'fa-image',
        variant.has_image ? 'Заменить или удалить картинку' : 'Прикрепить картинку',
        'Картинка варианта',
        function (event) {
          openImageMenu(prompt, variant, event.currentTarget);
        },
        'small'
      ),
      iconButton('fa-pen', 'Изменить вариант', 'Редактировать текст варианта', function () {
        openVariantDialog(prompt, variant);
      }, 'small'),
      iconButton('fa-trash-can', 'Удалить вариант', 'Переместить вариант в .trash/', function () {
        confirmDeleteVariant(prompt, variant);
      }, 'small danger-text')
    );

    row.appendChild(badge);

    if (variant.has_image) {
      const thumb = document.createElement('img');
      thumb.className = 'variant-thumb';
      thumb.alt = variant.description || 'Картинка варианта';
      thumb.title = 'Открыть картинку';
      thumb.dataset.prompt = prompt.id;
      thumb.dataset.variant = variant.id;
      thumb.dataset.rev = variant.image_rev || '';
      thumb.addEventListener('click', function () {
        openImageViewer(prompt, variant);
      });
      loadThumb(thumb);
      row.appendChild(thumb);
    }

    row.append(main, actions);
    return row;
  }

  /* --- боковая панель -------------------------------------------------- */

  function setSidebarCollapsed(collapsed) {
    state.sidebarCollapsed = collapsed;
    dom.sidebar.classList.toggle('collapsed', collapsed);
    safeStorageSet(STORAGE.collapsed, collapsed ? '1' : '0');
  }

  /* --- модальные окна -------------------------------------------------- */

  function openFormModal(config) {
    if (state.modal) {
      closeModal();
    }

    dom.modalTitle.textContent = config.title || '';
    dom.modalMessage.textContent = config.message || '';
    dom.modalMessage.hidden = !config.message;
    dom.modalFields.textContent = '';
    dom.modalError.textContent = '';
    dom.modalSubmit.textContent = config.submitLabel || 'Сохранить';
    dom.modalSubmit.hidden = config.submitVisible === false;
    dom.modalSubmit.classList.toggle('danger', Boolean(config.danger));
    dom.modalSubmit.disabled = false;
    dom.modalCancel.textContent = config.cancelLabel || 'Отмена';

    const inputs = new Map();
    const required = new Set();
    let datalist = null;

    (config.fields || []).forEach(function (field) {
      if (field.datalist === 'folders' && !datalist) {
        datalist = document.createElement('datalist');
        datalist.id = 'folder-paths-list';
        state.folderPaths.forEach(function (path) {
          const option = document.createElement('option');
          option.value = path;
          datalist.appendChild(option);
        });
        dom.modalFields.appendChild(datalist);
      }

      const wrapper = document.createElement('div');
      wrapper.className = 'field';

      const label = document.createElement('label');
      label.textContent = field.label || field.name;
      label.htmlFor = 'field-' + field.name;

      const isTextarea = field.type === 'textarea';
      const input = document.createElement(isTextarea ? 'textarea' : 'input');
      input.id = 'field-' + field.name;
      input.name = field.name;
      input.value = field.value === undefined || field.value === null ? '' : String(field.value);
      if (field.placeholder) {
        input.placeholder = field.placeholder;
      }
      if (isTextarea) {
        input.rows = field.rows || 8;
        input.className = field.mono ? 'mono' : '';
      } else {
        input.type = 'text';
        if (field.maxlength) {
          input.maxLength = field.maxlength;
        }
      }
      if (field.datalist === 'folders') {
        input.setAttribute('list', 'folder-paths-list');
        input.autocomplete = 'off';
      }

      wrapper.append(label, input);

      if (field.hint) {
        const hint = document.createElement('span');
        hint.className = 'hint';
        hint.textContent = field.hint;
        wrapper.appendChild(hint);
      }

      dom.modalFields.appendChild(wrapper);
      inputs.set(field.name, input);
      if (field.required) {
        required.add(field.name);
      }
    });

    if (config.content) {
      dom.modalFields.appendChild(config.content);
    }

    state.modal = {
      inputs: inputs,
      required: required,
      onSubmit: config.onSubmit,
      busy: false,
      config: config,
    };

    dom.modalRoot.hidden = false;
    const first = dom.modalFields.querySelector('input, textarea');
    if (first) {
      first.focus();
      first.select();
    } else {
      dom.modalSubmit.focus();
    }
  }

  function closeModal() {
    state.modal = null;
    dom.modalRoot.hidden = true;
    dom.modalFields.textContent = '';
    dom.modalMessage.textContent = '';
    dom.modalError.textContent = '';
    dom.modalSubmit.classList.remove('danger');
    dom.modalSubmit.hidden = false;
    dom.modalCancel.textContent = 'Отмена';
  }

  async function handleModalSubmit(event) {
    event.preventDefault();
    const modal = state.modal;
    if (!modal || modal.busy || !modal.onSubmit) {
      return;
    }

    const values = {};
    let invalid = null;
    modal.inputs.forEach(function (input, name) {
      const value = input.value;
      values[name] = value;
      input.classList.remove('invalid');
      if (modal.required.has(name) && !value.trim() && !invalid) {
        invalid = input;
      }
    });

    if (invalid) {
      invalid.classList.add('invalid');
      invalid.focus();
      dom.modalError.textContent = 'Заполните обязательное поле';
      return;
    }

    modal.busy = true;
    dom.modalSubmit.disabled = true;
    dom.modalError.textContent = '';

    try {
      await modal.onSubmit(values);
    } catch (error) {
      dom.modalError.textContent = error && error.message ? error.message : String(error);
      modal.busy = false;
      dom.modalSubmit.disabled = false;
      return;
    }
    closeModal();
  }

  function openPromptDialog(prompt) {
    const isEdit = Boolean(prompt);
    const fields = [
      {
        name: 'title',
        label: 'Заголовок',
        value: prompt ? prompt.title : '',
        placeholder: 'Например, «Ревью Python-кода»',
        required: true,
        maxlength: 200,
      },
      {
        name: 'tags',
        label: 'Теги',
        value: prompt ? (prompt.tags || []).join(', ') : '',
        placeholder: 'код, python',
        hint: 'Через запятую. Пустые и повторные теги отбрасываются.',
      },
      {
        name: 'folder',
        label: 'Папка',
        value: prompt ? prompt.folder || '' : state.selectedFolder || '',
        placeholder: 'Работа/Клиенты',
        datalist: 'folders',
        hint: 'Пусто — корень. Новое значение создаст папку с вложенностью.',
      },
    ];

    if (!isEdit) {
      fields.push(
        {
          name: 'description',
          label: 'Описание первого варианта',
          value: '',
          placeholder: 'Чем этот вариант отличается',
        },
        {
          name: 'text',
          label: 'Текст промта',
          value: '',
          type: 'textarea',
          rows: 10,
          mono: true,
        }
      );
    }

    openFormModal({
      title: isEdit ? 'Изменить промт' : 'Новый промт',
      fields: fields,
      submitLabel: isEdit ? 'Сохранить' : 'Создать',
      onSubmit: async function (values) {
        const tags = splitTags(values.tags);
        if (isEdit) {
          await call('update_prompt', prompt.id, values.title, tags, values.folder);
          setStatus('Промт сохранён');
        } else {
          const created = await call(
            'create_prompt',
            values.title,
            tags,
            values.folder,
            values.description,
            values.text
          );
          // Показываем папку, в которую сохранили, чтобы промт не «потерялся».
          state.selectedFolder = created.folder || '';
          safeStorageSet(STORAGE.folder, state.selectedFolder);
          state.query = '';
          state.selectedTags.clear();
          dom.searchInput.value = '';
          expandPath(state.selectedFolder);
          setStatus('Промт создан');
        }
        await refresh();
      },
    });
  }

  async function openVariantDialog(prompt, variant) {
    let current = { description: '', text: '' };
    if (variant) {
      try {
        const data = await call('read_variant', prompt.id, variant.id);
        current = { description: data.description, text: data.text };
      } catch (error) {
        showBanner('Не удалось открыть вариант', error.message, []);
        setStatus('Ошибка');
        return;
      }
    }

    openFormModal({
      title: variant ? 'Изменить вариант' : 'Новый вариант',
      message: 'Промт: ' + prompt.title,
      fields: [
        {
          name: 'description',
          label: 'Краткое описание варианта',
          value: current.description,
          placeholder: 'Например: «Строгий ревьюер, без похвал»',
        },
        {
          name: 'text',
          label: 'Текст промта',
          value: current.text,
          type: 'textarea',
          rows: 14,
          mono: true,
        },
      ],
      submitLabel: variant ? 'Сохранить' : 'Добавить',
      onSubmit: async function (values) {
        if (variant) {
          await call('update_variant', prompt.id, variant.id, values.description, values.text);
          setStatus('Вариант сохранён');
        } else {
          await call('add_variant', prompt.id, values.description, values.text);
          setStatus('Вариант добавлен');
        }
        await refresh();
      },
    });
  }

  function openConfirm(config) {
    openFormModal({
      title: config.title,
      message: config.message,
      fields: [],
      submitLabel: config.submitLabel || 'Удалить',
      danger: config.danger !== false,
      onSubmit: config.onConfirm,
    });
  }

  function confirmDeletePrompt(prompt) {
    const variants = (prompt.variants || []).length;
    openConfirm({
      title: 'Переместить промт в корзину?',
      message:
        '«' +
        prompt.title +
        '» и ' +
        countLabel(variants, ['вариант', 'варианта', 'вариантов']) +
        ' переедут в .trash/ с отметкой времени. Восстановить можно вручную из этой папки.',
      submitLabel: 'В корзину',
      onConfirm: async function () {
        await call('delete_prompt', prompt.id);
        setStatus('Промт перемещён в .trash/');
        await refresh();
      },
    });
  }

  function confirmDeleteVariant(prompt, variant) {
    openConfirm({
      title: 'Переместить вариант в корзину?',
      message:
        'Вариант «' +
        (variant.description || 'без описания') +
        '» будет перемещён в .trash/. Если вариант единственный, приложение попросит удалить промт целиком.',
      submitLabel: 'В корзину',
      onConfirm: async function () {
        await call('delete_variant', prompt.id, variant.id);
        setStatus('Вариант перемещён в .trash/');
        await refresh();
      },
    });
  }

  function openRenameFolder(node) {
    openFormModal({
      title: 'Переименовать папку',
      message: 'Промтов в папке: ' + node.total + '. Вложенные папки сохранят структуру.',
      fields: [
        {
          name: 'path',
          label: 'Новый путь',
          value: node.path,
          required: true,
          datalist: 'folders',
          hint: 'Например: «Работа/Клиенты». Можно переместить папку в другую ветку.',
        },
      ],
      submitLabel: 'Переименовать',
      onSubmit: async function (values) {
        const result = await call('rename_folder', node.path, values.path);
        setStatus('Папка переименована: ' + result.folder);
        await refresh();
      },
    });
  }

  function confirmDeleteFolder(node) {
    const parent = node.path.indexOf('/') >= 0 ? node.path.slice(0, node.path.lastIndexOf('/')) : '';
    const destination = parent ? '«' + parent + '»' : 'корень';
    openConfirm({
      title: 'Убрать папку «' + node.path + '»?',
      message:
        'Ничего не удаляется: ' +
        countLabel(node.total, ['промт', 'промта', 'промтов']) +
        ' переедут в ' +
        destination +
        '. Вложенные подпапки сохранят структуру.',
      submitLabel: 'Переместить',
      onConfirm: async function () {
        const result = await call('delete_folder', node.path);
        setStatus('Промты перенесены в ' + (result.to ? '«' + result.to + '»' : 'корень'));
        await refresh();
      },
    });
  }

  /* --- контекстное меню папки ------------------------------------------ */

  function openFolderMenu(node, anchor) {
    closeMenu();

    const menu = document.createElement('div');
    menu.className = 'menu';

    const rename = document.createElement('button');
    rename.type = 'button';
    rename.className = 'menu-item';
    rename.append(makeIcon('fa-pen'), document.createTextNode('Переименовать…'));
    rename.addEventListener('click', function () {
      closeMenu();
      openRenameFolder(node);
    });

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'menu-item danger';
    remove.append(makeIcon('fa-arrow-up'), document.createTextNode('Убрать папку (промты выше)'));
    remove.addEventListener('click', function () {
      closeMenu();
      confirmDeleteFolder(node);
    });

    menu.append(rename, remove);
    document.body.appendChild(menu);

    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - menuRect.width - 12));
    const top = Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - menuRect.height - 12));
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';

    state.menu = menu;
    state.menuHandler = function (event) {
      if (state.menu && !state.menu.contains(event.target)) {
        closeMenu();
      }
    };
    setTimeout(function () {
      document.addEventListener('mousedown', state.menuHandler);
    }, 0);
  }

  function openImageMenu(prompt, variant, anchor) {
    closeMenu();

    const menu = document.createElement('div');
    menu.className = 'menu';

    const attach = document.createElement('button');
    attach.type = 'button';
    attach.className = 'menu-item';
    attach.append(
      makeIcon('fa-image'),
      document.createTextNode(variant.has_image ? 'Заменить картинку' : 'Прикрепить картинку')
    );
    attach.addEventListener('click', function () {
      closeMenu();
      attachImage(prompt, variant);
    });
    menu.appendChild(attach);

    if (variant.has_image) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'menu-item danger';
      remove.append(makeIcon('fa-trash-can'), document.createTextNode('Удалить картинку'));
      remove.addEventListener('click', function () {
        closeMenu();
        confirmRemoveImage(prompt, variant);
      });
      menu.appendChild(remove);
    }

    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - menuRect.width - 12));
    const top = Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - menuRect.height - 12));
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';

    state.menu = menu;
    state.menuHandler = function (event) {
      if (state.menu && !state.menu.contains(event.target)) {
        closeMenu();
      }
    };
    setTimeout(function () {
      document.addEventListener('mousedown', state.menuHandler);
    }, 0);
  }

  async function attachImage(prompt, variant) {
    setStatus('Выбор файла…');
    try {
      const result = await call('attach_image', prompt.id, variant.id);
      if (!result || result.cancelled) {
        setStatus('Готово');
        return;
      }
      state.imageCache.clear();
      setStatus('Картинка прикреплена: 500×500 PNG');
      await refresh();
    } catch (error) {
      setStatus('Ошибка');
      showBanner('Не удалось прикрепить картинку', error.message, []);
    }
  }

  function confirmRemoveImage(prompt, variant) {
    openConfirm({
      title: 'Удалить картинку варианта?',
      message: 'PNG переедет в .trash/images с отметкой времени. Текст промта не изменится.',
      submitLabel: 'Удалить картинку',
      onConfirm: async function () {
        await call('remove_image', prompt.id, variant.id);
        state.imageCache.clear();
        setStatus('Картинка удалена');
        await refresh();
      },
    });
  }

  async function openImageViewer(prompt, variant) {
    try {
      const data = await call('get_variant_image', prompt.id, variant.id);
      if (!data || !data.data_url) {
        setStatus('Картинка недоступна');
        return;
      }

      const wrap = document.createElement('div');
      wrap.className = 'image-viewer';
      const image = document.createElement('img');
      image.src = data.data_url;
      image.alt = variant.description || 'Картинка варианта';
      wrap.appendChild(image);

      openFormModal({
        title: prompt.title,
        message: variant.description || 'Картинка варианта 500×500',
        content: wrap,
        fields: [],
        submitVisible: false,
        cancelLabel: 'Закрыть',
      });
    } catch (error) {
      showBanner('Не удалось открыть картинку', error.message, []);
    }
  }

  function closeMenu() {
    if (state.menuHandler) {
      document.removeEventListener('mousedown', state.menuHandler);
      state.menuHandler = null;
    }
    if (state.menu) {
      state.menu.remove();
      state.menu = null;
    }
  }

  /* --- запуск ---------------------------------------------------------- */

  function waitForApi(timeoutMs) {
    return new Promise(function (resolve, reject) {
      const started = Date.now();
      (function poll() {
        if (apiObject('get_state')) {
          resolve();
          return;
        }
        if (Date.now() - started > timeoutMs) {
          reject(new ApiError('pywebview не построил API за отведённое время.', 'no_bridge'));
          return;
        }
        setTimeout(poll, 60);
      })();
    });
  }

  async function boot() {
    if (state.booted) {
      return;
    }
    state.booted = true;
    await refresh();
  }

  dom.btnReload.addEventListener('click', function () {
    refresh();
  });

  dom.btnNew.addEventListener('click', function () {
    openPromptDialog(null);
  });

  dom.btnSync.addEventListener('click', function () {
    runSync();
  });

  dom.btnTestDav.addEventListener('click', function () {
    testDavConnection();
  });

  dom.btnEmptyNew.addEventListener('click', function () {
    openPromptDialog(null);
  });

  dom.searchInput.addEventListener('input', function () {
    state.query = dom.searchInput.value;
    dom.searchClear.hidden = !state.query;
    dom.resetFilters.hidden = !(state.query || state.selectedTags.size || state.selectedFolder);
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(function () {
      refresh();
    }, 250);
  });

  dom.searchInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      window.clearTimeout(searchTimer);
      refresh();
      return;
    }
    if (event.key === 'Escape' && state.query) {
      event.preventDefault();
      event.stopPropagation();
      window.clearTimeout(searchTimer);
      clearSearch();
    }
  });

  dom.searchClear.addEventListener('click', function () {
    window.clearTimeout(searchTimer);
    clearSearch();
    dom.searchInput.focus();
  });

  dom.tagsClear.addEventListener('click', function () {
    state.selectedTags.clear();
    renderTagCloud();
    renderSortControls();
    refresh();
  });

  dom.sortSelect.addEventListener('change', function () {
    const parsed = parseSort(dom.sortSelect.value);
    state.sort = parsed.sort;
    state.order = parsed.order;
    safeStorageSet(STORAGE.sort, dom.sortSelect.value);
    refresh();
  });

  dom.resetFilters.addEventListener('click', resetFilters);
  dom.emptyReset.addEventListener('click', resetFilters);

  dom.bannerClose.addEventListener('click', function () {
    hideBanner();
  });

  dom.btnSidebar.addEventListener('click', function () {
    setSidebarCollapsed(!state.sidebarCollapsed);
  });

  dom.btnCollapse.addEventListener('click', function () {
    setSidebarCollapsed(true);
  });

  dom.btnExpand.addEventListener('click', function () {
    setSidebarCollapsed(false);
  });

  dom.modalForm.addEventListener('submit', handleModalSubmit);

  dom.modalRoot.addEventListener('click', function (event) {
    if (event.target instanceof Element && event.target.closest('[data-close]')) {
      closeModal();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      if (state.menu) {
        closeMenu();
        return;
      }
      if (state.modal) {
        event.preventDefault();
        closeModal();
      }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'b') {
      event.preventDefault();
      setSidebarCollapsed(!state.sidebarCollapsed);
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
      event.preventDefault();
      setSidebarCollapsed(false);
      dom.searchInput.focus();
      dom.searchInput.select();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n') {
      event.preventDefault();
      if (!state.modal) {
        openPromptDialog(null);
      }
      return;
    }
    if (event.key === 'F5') {
      event.preventDefault();
      refresh();
    }
  });

  document.addEventListener('pywebviewready', function () {
    boot();
  });

  window.addEventListener('DOMContentLoaded', function () {
    setSidebarCollapsed(state.sidebarCollapsed);
    waitForApi(10000)
      .then(boot)
      .catch(function (error) {
        state.booted = true;
        setBusy(false);
        dom.loading.hidden = true;
        showBanner('Нет связи с Python', error.message, []);
        setStatus('Нет связи с Python');
      });
  });
})();
