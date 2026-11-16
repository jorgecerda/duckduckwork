/* DuckDuckWork Dashboard JavaScript Logic */

function getActiveSessionId() {
  const pathSegments = window.location.pathname.split('/').filter(Boolean);
  const reserved = ['static', 'api', 'dashboard.html', 'index.html', 'resumes', 'logs'];
  let sid = null;
  if (pathSegments.length === 2 && pathSegments[0] === 's') {
    sid = pathSegments[1];
  } else if (pathSegments.length === 1 && !reserved.includes(pathSegments[0]) && pathSegments[0] !== 's') {
    sid = pathSegments[0];
  }
  if (!sid) {
    sid = new URLSearchParams(window.location.search).get('s');
  }
  if (!sid) {
    sid = localStorage.getItem('applypilot_session_id') || 'default';
  }
  if (sid && sid.startsWith('ap_')) {
    sid = sid.substring(3);
  }
  return sid;
}

(function() {
  const originalFetch = window.fetch;
  window.fetch = function(url, options) {
    options = options || {};
    if (typeof url === 'string' && url.startsWith('/api/')) {
      const sid = getActiveSessionId();
      options.headers = options.headers || {};
      if (options.headers instanceof Headers) {
        options.headers.set('X-Session-ID', sid);
      } else {
        options.headers['X-Session-ID'] = sid;
      }
      if (!url.includes('s=')) {
        const sep = url.includes('?') ? '&' : '?';
        url = url + sep + 's=' + sid;
      }
    }
    return originalFetch.call(this, url, options);
  };
})();

let selectedSearchProfile = '';
let selectedScore = 'all';
let statusFilter = 'all';
let selectedScores = new Set();
let selectedSources = new Set();
let selectedCompanies = new Set();
let selectedCountries = new Set();
let selectedWorkModes = new Set();
let selectedAppTypes = new Set();
let searchText = '';
if (typeof window.allSearchProfiles === 'undefined') { window.allSearchProfiles = []; }
var allSearchProfiles = window.allSearchProfiles;
let isRunnerActive = false;
let runningSearchProfileId = localStorage.getItem('applypilot_running_search_id') || null;
let runnerInterval = null;

function updateSearchTabsRunningState(runningId, isRunning) {
  const tabs = document.querySelectorAll('.search-tab-btn[data-profile-id]');
  tabs.forEach(tab => {
    const pId = tab.dataset.profileId;
    const shouldRun = isRunning && (runningId === 'all' || pId === runningId);
    if (shouldRun) {
      tab.classList.add('tab-running');
      tab.setAttribute('aria-busy', 'true');
      let spinner = tab.querySelector('.tab-spinner');
      if (!spinner) {
        spinner = document.createElement('span');
        spinner.className = 'tab-spinner';
        spinner.setAttribute('aria-hidden', 'true');
        tab.insertBefore(spinner, tab.firstChild);
      }
    } else {
      tab.classList.remove('tab-running');
      tab.removeAttribute('aria-busy');
      const spinner = tab.querySelector('.tab-spinner');
      if (spinner) {
        spinner.remove();
      }
    }
  });
  updateSearchTabBadges();
}

function updateSearchTabBadges() {
  const cards = document.querySelectorAll('.job-card');
  const tabCounts = {};

  const profileMap = new Map();
  const profileScoreMap = new Map();
  (window.allSearchProfiles || allSearchProfiles || []).forEach(p => {
    if (p && p.id) {
      const bList = Array.isArray(p.blocked_posters) ? p.blocked_posters : (p.blocked_posters ? String(p.blocked_posters).split(',') : []);
      profileMap.set(p.id, bList.map(b => b.trim().toLowerCase().replace(/[^a-z0-9]/g, '')).filter(Boolean));

      const rawSf = p.score_filters;
      const sfList = (Array.isArray(rawSf) && rawSf.length > 0)
        ? rawSf.map(s => String(s).toLowerCase().trim())
        : ['high', 'solid', 'moderate'];
      profileScoreMap.set(p.id, sfList);
    }
  });

  // 1. Calculate active new jobs per search profile from live DOM cards
  cards.forEach(card => {
    const sId = card.dataset.searchId || 'search_1';
    const isSaved = card.dataset.saved === 'true';
    const isApplied = card.dataset.status === 'applied';
    const isArchived = card.dataset.archived === 'true';

    const comp = (card.dataset.company || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const blockedList = profileMap.get(sId) || [];
    const isBlocked = (comp && blockedList.some(b => comp.includes(b) || b.includes(comp))) || card.dataset.isBlocked === 'true';

    const scoreFilters = profileScoreMap.get(sId) || ['high', 'solid', 'moderate'];
    const cardScoreCategory = (card.dataset.scoreCategory || '').toLowerCase();
    const isScoreAllowed = scoreFilters.includes(cardScoreCategory);

    if (!isArchived && !isApplied && !isSaved && !isBlocked && isScoreAllowed) {
      tabCounts[sId] = (tabCounts[sId] || 0) + 1;
    }
  });

  // 2. Fall back to saved localStorage counts only if tab has no cards loaded in DOM yet
  let savedCounts = {};
  try {
    savedCounts = JSON.parse(localStorage.getItem('ddw_tab_new_counts') || '{}');
  } catch(e) {}

  document.querySelectorAll('.search-tab-btn[data-profile-id]').forEach(tab => {
    const pId = tab.dataset.profileId;
    const hasCardsInDom = document.querySelector(`.job-card[data-search-id="${pId}"]`) !== null;
    const count = hasCardsInDom ? (tabCounts[pId] || 0) : (savedCounts[pId] || 0);

    let badge = tab.querySelector('.tab-new-badge');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'tab-new-badge';
      const spinner = tab.querySelector('.tab-spinner');
      if (spinner && spinner.nextSibling) {
        tab.insertBefore(badge, spinner.nextSibling);
      } else {
        tab.insertBefore(badge, tab.firstChild);
      }
    }

    badge.textContent = count;
    if (count > 0) {
      badge.classList.remove('zero');
      badge.classList.add('has-new');
      badge.title = `${count} new job${count === 1 ? '' : 's'} available to review`;
    } else {
      badge.classList.remove('has-new');
      badge.classList.add('zero');
      badge.title = 'No new jobs to review';
    }
  });

  try {
    localStorage.setItem('ddw_tab_new_counts', JSON.stringify(tabCounts));
  } catch(e) {}
}

function updateRunnerBannerVisibility(message, runnerData) {
  const banner = document.getElementById('runner-status-banner');
  const statusTxt = document.getElementById('runner-status-text');
  const progressWrap = document.getElementById('runner-progress-bar-wrap');
  const progressFill = document.getElementById('runner-progress-bar-fill');
  if (!banner) return;

  const activeTabId = selectedSearchProfile;
  const currentRunningId = (runnerData && runnerData.search_id) ? runnerData.search_id : runningSearchProfileId;
  const isMatchingTab = isRunnerActive && currentRunningId && (currentRunningId === 'all' ? true : activeTabId === currentRunningId);

  if (isMatchingTab) {
    banner.style.display = 'flex';
    if (statusTxt && message) statusTxt.textContent = message;
    if (progressWrap && progressFill) {
      if (runnerData && runnerData.status === 'scoring' && runnerData.scoring_total > 0) {
        progressWrap.style.display = 'block';
        const pct = Math.min(100, Math.max(0, runnerData.scoring_percent || 0));
        progressFill.style.width = pct + '%';
        progressFill.setAttribute('aria-valuenow', pct);
      } else {
        progressWrap.style.display = 'none';
      }
    }
  } else {
    banner.style.display = 'none';
  }
}

function toggleSidebarSection(headerEl) {
  const section = headerEl.closest('.sidebar-section');
  if (!section) return;

  const isMobile = window.innerWidth <= 900;
  if (isMobile) {
    const isExpanded = section.classList.toggle('expanded');
    section.classList.toggle('collapsed', !isExpanded);
    const icon = section.querySelector('.section-toggle-icon');
    if (icon) icon.innerHTML = isExpanded ? '&minus;' : '+';
  } else {
    const isCollapsed = section.classList.toggle('collapsed');
    section.classList.toggle('expanded', !isCollapsed);
    const icon = section.querySelector('.section-toggle-icon');
    if (icon) icon.innerHTML = isCollapsed ? '+' : '&minus;';
  }
}

function toggleSubSection(headerEl) {
  const sub = headerEl.closest('.filter-sub-section');
  if (!sub) return;
  sub.classList.toggle('collapsed');
  const icon = sub.querySelector('.sub-toggle-icon');
  if (icon) {
    icon.innerHTML = sub.classList.contains('collapsed') ? '+' : '&minus;';
  }
}

function saveStateToLocalStorage() {
  localStorage.setItem('applypilot_selected_search_profile', selectedSearchProfile);
  localStorage.setItem('applypilot_status_filter', statusFilter);
  localStorage.setItem('applypilot_selected_scores', JSON.stringify(Array.from(selectedScores)));
  localStorage.setItem('applypilot_selected_sources', JSON.stringify(Array.from(selectedSources)));
  localStorage.setItem('applypilot_selected_companies', JSON.stringify(Array.from(selectedCompanies)));
  localStorage.setItem('applypilot_selected_countries', JSON.stringify(Array.from(selectedCountries)));
  localStorage.setItem('applypilot_selected_workmodes', JSON.stringify(Array.from(selectedWorkModes)));
  localStorage.setItem('applypilot_selected_apptypes', JSON.stringify(Array.from(selectedAppTypes)));
}

function resetAllSidebarFilters() {
  selectedScores.clear();
  selectedSources.clear();
  selectedCompanies.clear();
  selectedCountries.clear();
  selectedWorkModes.clear();
  selectedAppTypes.clear();
  searchText = '';
  statusFilter = 'all';
  const searchInput = document.getElementById('main-search-input');
  if (searchInput) searchInput.value = '';

  document.querySelectorAll('.status-nav .nav-item').forEach(b => {
    if (b.dataset.status === 'all') b.classList.add('active');
    else b.classList.remove('active');
  });

  restoreFilterButtonsUI();
  saveStateToLocalStorage();
  applyFilters();
}

function loadStateFromLocalStorage() {
  const savedProfile = localStorage.getItem('applypilot_selected_search_profile');
  const profilesList = window.allSearchProfiles || allSearchProfiles || [];
  const firstProfileId = (profilesList && profilesList.length > 0) ? (profilesList[0].id || 'search_1') : 'search_1';
  if (savedProfile && savedProfile !== 'all') {
    const exists = (profilesList || []).some(p => (p.id || 'search_1') === savedProfile);
    if (exists) {
      selectedSearchProfile = savedProfile;
    } else {
      selectedSearchProfile = firstProfileId;
    }
  } else {
    selectedSearchProfile = firstProfileId;
  }

  const savedStatus = localStorage.getItem('applypilot_status_filter');
  if (savedStatus && ['all', 'saved', 'applied', 'archived'].includes(savedStatus)) {
    statusFilter = savedStatus;
  } else {
    statusFilter = 'all';
  }

  try {
    const scoreArr = JSON.parse(localStorage.getItem('applypilot_selected_scores') || '[]');
    selectedScores = new Set(scoreArr.filter(s => s && s !== 'all').map(s => String(s).toLowerCase()));

    const srcArr = JSON.parse(localStorage.getItem('applypilot_selected_sources') || '[]');
    selectedSources = new Set(srcArr.filter(s => s && s !== 'all').map(s => String(s).toLowerCase()));
    
    const compArr = JSON.parse(localStorage.getItem('applypilot_selected_companies') || '[]');
    selectedCompanies = new Set(compArr.filter(s => s && s !== 'all').map(s => String(s).toLowerCase()));
    
    const cntArr = JSON.parse(localStorage.getItem('applypilot_selected_countries') || '[]');
    selectedCountries = new Set(cntArr.filter(s => s && s !== 'all').map(s => String(s).toLowerCase()));
    
    const wmArr = JSON.parse(localStorage.getItem('applypilot_selected_workmodes') || '[]');
    selectedWorkModes = new Set(wmArr.filter(s => s && s !== 'all').map(s => String(s).toLowerCase()));

    const atArr = JSON.parse(localStorage.getItem('applypilot_selected_apptypes') || '[]');
    selectedAppTypes = new Set(atArr.filter(s => s && s !== 'all').map(s => String(s).toLowerCase()));
  } catch(e) {
    selectedScores.clear();
    selectedSources.clear();
    selectedCompanies.clear();
    selectedCountries.clear();
    selectedWorkModes.clear();
    selectedAppTypes.clear();
  }
}

function safeDecodeText(str) {
  if (!str) return '';
  try {
    let s = String(str);
    let prev = null;
    while (s !== prev && s.includes('%')) {
      prev = s;
      s = decodeURIComponent(s.replace(/\+/g, ' '));
    }
    return s;
  } catch(e) {
    return String(str);
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatRecencyText(hours) {
  const h = parseInt(hours, 10) || 24;
  if (h === 24) return 'Past 24 hours';
  if (h === 48) return 'Past 48 hours';
  if (h === 168) return 'Past 7 days';
  if (h === 720) return 'Past 30 days';
  if (h % 24 === 0 && h > 0) return `Past ${h / 24} days`;
  if (h > 0) return `Past ${h} hours`;
  return 'Past 24 hours';
}

function formatSearchUpdatedText(lastRun) {
  if (!lastRun) {
    return 'Search not run yet';
  }
  let lastTimeMs = 0;
  if (typeof lastRun === 'number') {
    lastTimeMs = lastRun < 1e11 ? lastRun * 1000 : lastRun;
  } else if (typeof lastRun === 'string') {
    const num = Number(lastRun);
    if (!isNaN(num) && num > 0) {
      lastTimeMs = num < 1e11 ? num * 1000 : num;
    } else {
      lastTimeMs = Date.parse(lastRun);
    }
  }
  if (!lastTimeMs || isNaN(lastTimeMs) || lastTimeMs <= 0) {
    return 'Search not run yet';
  }

  const nowMs = Date.now();
  const diffSec = Math.max(0, Math.floor((nowMs - lastTimeMs) / 1000));
  const diffMin = Math.floor(diffSec / 60);
  const diffHours = Math.floor(diffMin / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMin < 1) {
    return 'Search Updated just now';
  }
  if (diffMin === 1) {
    return 'Search Updated 1 minute ago';
  }
  if (diffMin < 60) {
    return `Search Updated ${diffMin} minutes ago`;
  }
  if (diffHours === 1) {
    return 'Search Updated 1 hour ago';
  }
  if (diffHours < 24) {
    return `Search Updated ${diffHours} hours ago`;
  }
  if (diffDays === 1) {
    return 'Search Updated 1 day ago';
  }
  return `Search Updated ${diffDays} days ago`;
}

function updateRightPanelHeader(val) {
  const titleEl = document.getElementById('main-panel-title');
  const subEl = document.getElementById('main-panel-subtitle');
  const updatedTextEl = document.getElementById('search-updated-timestamp-text');
  const editBtn = document.getElementById('header-edit-search-btn');
  const runBtn = document.getElementById('header-run-search-btn');

  const profilesList = window.allSearchProfiles || allSearchProfiles || [];
  let targetVal = val;

  if ((!targetVal || targetVal === 'all') && profilesList && profilesList.length > 0) {
    targetVal = profilesList[0].id || 'search_1';
  }

  const showHeaderActions = (targetVal && targetVal !== 'all') || (profilesList && profilesList.length > 0);
  if (editBtn) editBtn.style.display = showHeaderActions ? 'inline-flex' : 'none';
  if (runBtn) runBtn.style.display = showHeaderActions ? 'inline-flex' : 'none';

  if (!titleEl || !subEl) return;

  if (targetVal === 'all') {
    titleEl.textContent = "All Search Profiles";
    subEl.innerHTML = "Showing roles across all configured search profiles &middot; 24h Daily Feed";
    if (updatedTextEl) {
      const maxLastRun = (profilesList || []).reduce((max, p) => Math.max(max, Number(p.last_run) || 0), 0);
      updatedTextEl.textContent = maxLastRun > 0 ? formatSearchUpdatedText(maxLastRun) : "Search not run yet";
    }
    return;
  }

  const sp = (profilesList || []).find(p => (p.id || 'search_1') === targetVal) || (profilesList && profilesList[0]);
  if (sp) {
    const qStr = Array.isArray(sp.queries) ? sp.queries.map(safeDecodeText).join(', ') : safeDecodeText(sp.queries || '');
    const lStr = Array.isArray(sp.locations) ? sp.locations.map(safeDecodeText).join(', ') : safeDecodeText(sp.locations || '');
    const tStr = Array.isArray(sp.job_types) ? sp.job_types.map(safeDecodeText).join(', ') : safeDecodeText(sp.job_types || sp.types || '');
    const recencyStr = formatRecencyText(sp.hours_old);

    const pName = safeDecodeText(sp.name || "Search Profile");
    titleEl.textContent = pName;
    document.title = `${pName} - DuckDuckWork`;
    let sub = `Targeting ${lStr || 'Worldwide'}`;
    if (tStr) sub += ` (${tStr})`;
    if (qStr) sub += ` &middot; Queries: ${qStr}`;
    sub += ` &middot; ${recencyStr}`;
    subEl.innerHTML = sub;

    if (updatedTextEl) {
      updatedTextEl.textContent = formatSearchUpdatedText(sp.last_run);
    }
  } else {
    titleEl.textContent = "Search Profile";
    subEl.innerHTML = "Targeting selected criteria &middot; Past 24 hours";
    if (updatedTextEl) {
      updatedTextEl.textContent = "Search not run yet";
    }
  }
}

function filterSearchProfile(profileId) {
  selectedSearchProfile = profileId;
  selectedScores.clear();
  selectedSources.clear();
  selectedCompanies.clear();
  selectedCountries.clear();
  selectedWorkModes.clear();
  selectedAppTypes.clear();
  searchText = '';
  const searchInput = document.getElementById('main-search-input');
  if (searchInput) searchInput.value = '';

  restoreFilterButtonsUI();
  saveStateToLocalStorage();

  document.querySelectorAll('.search-tab-btn').forEach(b => {
    if (b.dataset.profileId === profileId) {
      b.classList.add('active');
    } else {
      b.classList.remove('active');
    }
  });

  updateRightPanelHeader(profileId);
  updateRunnerBannerVisibility();
  applyFilters();
}

function filterStatus(status, btn) {
  statusFilter = status;
  saveStateToLocalStorage();
  document.querySelectorAll('.status-nav .nav-item').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  applyFilters();
}

function filterText(text) {
  searchText = text.toLowerCase().trim();
  applyFilters();
}

function clearSearchInput() {
  searchText = '';
  const searchInput = document.getElementById('main-search-input');
  if (searchInput) searchInput.value = '';
  applyFilters();
}

function toggleCheckboxFilter(type, value, inputEl) {
  if (value === 'all') {
    clearMultiFilter(type);
    return;
  }

  let targetSet;
  if (type === 'scores') targetSet = selectedScores;
  else if (type === 'sources') targetSet = selectedSources;
  else if (type === 'companies') targetSet = selectedCompanies;
  else if (type === 'countries') targetSet = selectedCountries;
  else if (type === 'workmodes') targetSet = selectedWorkModes;
  else if (type === 'apptypes') targetSet = selectedAppTypes;

  if (!targetSet) return;

  const valLower = value.toLowerCase();
  if (inputEl ? inputEl.checked : !targetSet.has(valLower)) {
    targetSet.add(valLower);
  } else {
    targetSet.delete(valLower);
  }

  restoreFilterButtonsUI();
  saveStateToLocalStorage();
  applyFilters();
}

function selectAllMultiFilter(type) {
  let targetSet;
  if (type === 'scores') targetSet = selectedScores;
  else if (type === 'sources') targetSet = selectedSources;
  else if (type === 'countries') targetSet = selectedCountries;
  else if (type === 'workmodes') targetSet = selectedWorkModes;
  else if (type === 'apptypes') targetSet = selectedAppTypes;
  else if (type === 'companies') targetSet = selectedCompanies;

  if (targetSet) {
    targetSet.clear();
    document.querySelectorAll(`.filter-checkbox-input[data-filter-type="${type}"]`).forEach(input => {
      const val = (input.dataset.filterVal || '').toLowerCase();
      if (val && val !== 'all') {
        targetSet.add(val);
      }
    });
  }

  restoreFilterButtonsUI();
  saveStateToLocalStorage();
  applyFilters();
}

function clearMultiFilter(type) {
  let targetSet;
  if (type === 'scores') targetSet = selectedScores;
  else if (type === 'sources') targetSet = selectedSources;
  else if (type === 'countries') targetSet = selectedCountries;
  else if (type === 'workmodes') targetSet = selectedWorkModes;
  else if (type === 'apptypes') targetSet = selectedAppTypes;
  else if (type === 'companies') targetSet = selectedCompanies;

  if (targetSet) {
    targetSet.clear();
  }

  restoreFilterButtonsUI();
  saveStateToLocalStorage();
  applyFilters();
}

function updateSidebarCounters(profileCards, statusCards) {
  // 1. Update Application Status Nav Badges from profileCards
  let countNew = 0, countSaved = 0, countApplied = 0, countArchived = 0;
  profileCards.forEach(card => {
    const isSaved = card.dataset.saved === 'true';
    const isApplied = card.dataset.status === 'applied';
    const isArchived = card.dataset.archived === 'true';

    if (isArchived) countArchived++;
    if (isApplied) countApplied++;
    if (isSaved) countSaved++;
    if (!isArchived && !isApplied && !isSaved) countNew++;
  });

  const countAllEl = document.getElementById('count-all');
  if (countAllEl) countAllEl.textContent = countNew;
  const countSavedEl = document.getElementById('count-saved');
  if (countSavedEl) countSavedEl.textContent = countSaved;
  const countAppliedEl = document.getElementById('count-applied');
  if (countAppliedEl) countAppliedEl.textContent = countApplied;
  const countArchivedEl = document.getElementById('count-archived');
  if (countArchivedEl) countArchivedEl.textContent = countArchived;

  // Keep search tab count badges in lockstep sync with sidebar counters
  updateSearchTabBadges();

  // 2. Update Category Filter Checkbox Badges from statusCards
  const scoreCounts = { high: 0, solid: 0, moderate: 0, low: 0 };
  const apptypeCounts = { easy_apply: 0, website: 0 };
  const sourceCounts = {}, countryCounts = {}, workmodeCounts = {}, companyCounts = {}, companyDisplayNames = {};

  statusCards.forEach(card => {
    const isEa = card.dataset.isEasyApply === 'true' || card.dataset.appType === 'easy_apply';
    const atKey = isEa ? 'easy_apply' : 'website';
    apptypeCounts[atKey] = (apptypeCounts[atKey] || 0) + 1;

    const scoreCat = card.dataset.scoreCategory || 'low';
    if (scoreCat) scoreCounts[scoreCat] = (scoreCounts[scoreCat] || 0) + 1;

    const site = card.dataset.site || '';
    if (site) sourceCounts[site] = (sourceCounts[site] || 0) + 1;

    const country = card.dataset.country || '';
    if (country) countryCounts[country] = (countryCounts[country] || 0) + 1;

    const wm = card.dataset.workmode || '';
    if (wm) workmodeCounts[wm] = (workmodeCounts[wm] || 0) + 1;

    const companyKey = (card.dataset.company || '').toLowerCase().trim();
    if (companyKey) {
      companyCounts[companyKey] = (companyCounts[companyKey] || 0) + 1;
      if (!companyDisplayNames[companyKey]) {
        const rawName = card.dataset.companyName || card.querySelector('.company-name')?.textContent || companyKey;
        companyDisplayNames[companyKey] = rawName.trim();
      }
    }
  });

  const totalCount = statusCards.length;

  const updateBadges = (type, countsMap) => {
    let hasVisibleSpecificOptions = false;
    document.querySelectorAll(`.filter-checkbox-item[data-filter-type="${type}"]`).forEach(item => {
      const val = item.dataset.filterVal;
      const cnt = (val === 'all') ? totalCount : (countsMap[val] || 0);
      const badge = item.querySelector('.btn-badge');
      if (badge) badge.textContent = cnt;

      if (val !== 'all') {
        if (cnt === 0) {
          item.style.display = 'none';
        } else {
          item.style.display = 'flex';
          hasVisibleSpecificOptions = true;
        }
      }
    });

    const sampleItem = document.querySelector(`.filter-checkbox-item[data-filter-type="${type}"]`);
    if (sampleItem) {
      const subSection = sampleItem.closest('.filter-sub-section');
      if (subSection) {
        subSection.style.display = (totalCount > 0 && hasVisibleSpecificOptions) ? '' : 'none';
      }
    }
  };

  const formatSourceName = (key) => {
    const k = (key || '').toLowerCase().trim();
    if (k === 'linkedin') return 'LinkedIn';
    if (k === 'indeed') return 'Indeed';
    if (k === 'getonboard' || k === 'get on board') return 'Get on Board';
    if (k === 'remotive') return 'Remotive';
    if (k === 'weworkremotely') return 'WeWorkRemotely';
    if (k === 'hirewithnear' || k === 'hire with near' || k === 'near') return 'Hire With Near';
    return k ? (k.charAt(0).toUpperCase() + k.slice(1)) : 'Direct';
  };

  const updateSourcesFilterGrid = (countsMap, totalJobs) => {
    const gridEl = document.getElementById('filter-grid-sources');
    if (!gridEl) return;

    const standardSites = ['linkedin', 'indeed', 'getonboard', 'remotive', 'weworkremotely', 'hirewithnear'];
    const extraSites = Object.keys(countsMap).filter(k => !standardSites.includes(k) && !k.includes('get') && !k.includes('near'));
    const allSites = [...standardSites, ...extraSites];

    let html = `
      <label class="filter-checkbox-item ${selectedSources.size === 0 ? 'active' : ''}" data-filter-type="sources" data-filter-val="all">
        <input type="checkbox" class="filter-checkbox-input" data-filter-type="sources" data-filter-val="all" ${selectedSources.size === 0 ? 'checked' : ''} onchange="toggleCheckboxFilter('sources', 'all', this)">
        <span class="custom-checkbox"></span>
        <span class="filter-label-text">All Sources</span>
        <span class="btn-badge">${totalJobs}</span>
      </label>
    `;

    allSites.forEach(sKey => {
      let cnt = countsMap[sKey] || 0;
      if (sKey === 'getonboard') {
        cnt = (countsMap['getonboard'] || 0) + (countsMap['get on board'] || 0);
      }
      if (sKey === 'hirewithnear') {
        cnt = (countsMap['hirewithnear'] || 0) + (countsMap['hire with near'] || 0) + (countsMap['near'] || 0);
      }
      const isChecked = selectedSources.has(sKey) ||
        (sKey === 'getonboard' && selectedSources.has('get on board')) ||
        (sKey === 'hirewithnear' && (selectedSources.has('hire with near') || selectedSources.has('near')));
      const activeCls = isChecked ? ' active' : '';
      const checkedAttr = isChecked ? ' checked' : '';

      html += `
        <label class="filter-checkbox-item${activeCls}" data-filter-type="sources" data-filter-val="${escapeHtml(sKey)}">
          <input type="checkbox" class="filter-checkbox-input" data-filter-type="sources" data-filter-val="${escapeHtml(sKey)}"${checkedAttr} onchange="toggleCheckboxFilter('sources', '${escapeHtml(sKey).replace(/'/g, "\\'")}', this)">
          <span class="custom-checkbox"></span>
          <span class="filter-label-text">${escapeHtml(formatSourceName(sKey))}</span>
          <span class="btn-badge">${cnt}</span>
        </label>
      `;
    });

    gridEl.innerHTML = html;

    const subSection = gridEl.closest('.filter-sub-section');
    if (subSection) {
      const headerTitleEl = subSection.querySelector('.filter-sub-title');
      if (headerTitleEl) {
        headerTitleEl.textContent = `Sources (${allSites.length})`;
      }
      subSection.style.display = totalJobs > 0 ? '' : 'none';
    }
  };

  const updateCompanyFilterGrid = (countsMap, displayNamesMap, totalJobs) => {
    const gridEl = document.getElementById('filter-grid-companies');
    if (!gridEl) return;

    const keys = Object.keys(countsMap).sort((a, b) => countsMap[b] - countsMap[a]);
    let hasVisibleSpecificOptions = keys.length > 0;

    let html = `
      <label class="filter-checkbox-item ${selectedCompanies.size === 0 ? 'active' : ''}" data-filter-type="companies" data-filter-val="all">
        <input type="checkbox" class="filter-checkbox-input" data-filter-type="companies" data-filter-val="all" ${selectedCompanies.size === 0 ? 'checked' : ''} onchange="toggleCheckboxFilter('companies', 'all', this)">
        <span class="custom-checkbox"></span>
        <span class="filter-label-text">All Posters</span>
        <span class="btn-badge">${totalJobs}</span>
      </label>
    `;

    keys.forEach(k => {
      const cnt = countsMap[k];
      const name = displayNamesMap[k] || k;
      const isChecked = selectedCompanies.has(k);
      const activeCls = isChecked ? ' active' : '';
      const checkedAttr = isChecked ? ' checked' : '';

      html += `
        <label class="filter-checkbox-item${activeCls}" data-filter-type="companies" data-filter-val="${escapeHtml(k)}">
          <input type="checkbox" class="filter-checkbox-input" data-filter-type="companies" data-filter-val="${escapeHtml(k)}"${checkedAttr} onchange="toggleCheckboxFilter('companies', '${escapeHtml(k).replace(/'/g, "\\'")}', this)">
          <span class="custom-checkbox"></span>
          <span class="filter-label-text">${escapeHtml(name)}</span>
          <span class="btn-badge">${cnt}</span>
        </label>
      `;
    });

    gridEl.innerHTML = html;

    const subSection = gridEl.closest('.filter-sub-section');
    if (subSection) {
      const headerTitleEl = subSection.querySelector('.filter-sub-title');
      if (headerTitleEl) {
        headerTitleEl.textContent = `Job Poster (${keys.length})`;
      }
      subSection.style.display = (totalJobs > 0 && hasVisibleSpecificOptions) ? '' : 'none';
    }
  };

  updateBadges('scores', scoreCounts);
  updateSourcesFilterGrid(sourceCounts, totalCount);
  updateBadges('countries', countryCounts);
  updateBadges('workmodes', workmodeCounts);
  updateBadges('apptypes', apptypeCounts);
  updateCompanyFilterGrid(companyCounts, companyDisplayNames, totalCount);

  const mainFiltersSection = document.getElementById('main-filters-section');
  if (mainFiltersSection) {
    const visibleSubSections = mainFiltersSection.querySelectorAll('.filter-sub-section:not([style*="display: none"])');
    mainFiltersSection.style.display = (totalCount > 0 && visibleSubSections.length > 0) ? '' : 'none';
  }
}

function applyFilters() {
  if (!selectedSearchProfile || selectedSearchProfile === 'all') {
    const profilesList = window.allSearchProfiles || allSearchProfiles || [];
    selectedSearchProfile = (profilesList && profilesList.length > 0) ? (profilesList[0].id || 'search_1') : 'search_1';
  }
  console.log('[DEBUG JS] applyFilters running. selectedSearchProfile =', selectedSearchProfile, 'statusFilter =', statusFilter);
  const cards = document.querySelectorAll('.job-card');
  let shown = 0;
  const profileCards = [];
  const statusCards = [];

  const currentProfile = (window.allSearchProfiles || allSearchProfiles || []).find(p => p.id === selectedSearchProfile);
  const rawBlocked = currentProfile ? (currentProfile.blocked_posters || []) : [];
  const blockedList = (Array.isArray(rawBlocked) ? rawBlocked : String(rawBlocked).split(','))
    .map(b => b.trim().toLowerCase().replace(/[^a-z0-9]/g, ''))
    .filter(Boolean);

  const rawScoreFilters = currentProfile ? (currentProfile.score_filters || []) : [];
  const profileScoreFilters = (Array.isArray(rawScoreFilters) && rawScoreFilters.length > 0)
    ? rawScoreFilters.map(s => String(s).toLowerCase().trim())
    : ['high', 'solid', 'moderate'];

  cards.forEach(card => {
    const cardSearchId = card.dataset.searchId || 'search_1';
    const matchesProfile = (cardSearchId === selectedSearchProfile);

    if (!matchesProfile) {
      card.classList.add('hidden');
      card.style.display = 'none';
      return;
    }

    const companyClean = (card.dataset.company || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const isCompanyBlocked = (companyClean && blockedList.some(b => companyClean.includes(b) || b.includes(companyClean))) || card.dataset.isBlocked === 'true';
    if (isCompanyBlocked) {
      card.classList.add('hidden');
      card.style.display = 'none';
      return;
    }

    const cardScoreCategory = (card.dataset.scoreCategory || '').toLowerCase();
    const isScoreAllowedByProfile = profileScoreFilters.includes(cardScoreCategory);
    if (!isScoreAllowedByProfile) {
      card.classList.add('hidden');
      card.style.display = 'none';
      return;
    }

    profileCards.push(card);

    const isSaved = card.dataset.saved === 'true';
    const isApplied = card.dataset.status === 'applied';
    const isArchived = card.dataset.archived === 'true';

    let matchesStatus = false;
    if (statusFilter === 'all') matchesStatus = !isArchived && !isApplied && !isSaved;
    else if (statusFilter === 'saved') matchesStatus = isSaved;
    else if (statusFilter === 'applied') matchesStatus = isApplied;
    else if (statusFilter === 'archived') matchesStatus = isArchived;

    if (!matchesStatus) {
      card.classList.add('hidden');
      card.style.display = 'none';
      return;
    }

    statusCards.push(card);

    const scoreCat = (card.dataset.scoreCategory || '').toLowerCase();
    const matchesScore = (selectedScores.size === 0 || selectedScores.has(scoreCat));

    const siteVal = (card.dataset.site || '').toLowerCase();
    const isNearMatch = (siteVal.includes('near') || siteVal.includes('hirewithnear')) && (selectedSources.has('hirewithnear') || selectedSources.has('hire with near') || selectedSources.has('near'));
    const matchesSource = (selectedSources.size === 0 || selectedSources.has(siteVal) || (siteVal.includes('get') && (selectedSources.has('getonboard') || selectedSources.has('get on board'))) || isNearMatch);

    const companyVal = (card.dataset.company || '').toLowerCase();
    const matchesCompany = (selectedCompanies.size === 0 || selectedCompanies.has(companyVal));

    const countryVal = (card.dataset.country || '').toLowerCase();
    const matchesCountry = (selectedCountries.size === 0 || selectedCountries.has(countryVal));

    const wmVal = (card.dataset.workmode || '').toLowerCase();
    const matchesWorkMode = (selectedWorkModes.size === 0 || selectedWorkModes.has(wmVal));

    const isEa = card.dataset.isEasyApply === 'true' || card.dataset.appType === 'easy_apply';
    const appTypeVal = isEa ? 'easy_apply' : 'website';
    const matchesAppType = (selectedAppTypes.size === 0 || selectedAppTypes.has(appTypeVal));

    const text = card.textContent.toLowerCase();
    const matchesText = !searchText || text.includes(searchText);

    if (matchesScore && matchesSource && matchesCompany && matchesCountry && matchesWorkMode && matchesAppType && matchesText) {
      card.classList.remove('hidden');
      card.style.display = '';
      shown++;
    } else {
      card.classList.add('hidden');
      card.style.display = 'none';
    }
  });

  updateSidebarCounters(profileCards, statusCards);

  const totalStatusCards = statusCards.length;
  const jobCountEl = document.getElementById('job-count');
  if (jobCountEl) {
    if (totalStatusCards > 0) {
      jobCountEl.style.display = 'inline-block';
      if (shown === totalStatusCards) {
        jobCountEl.textContent = `Showing ${shown} jobs`;
      } else {
        jobCountEl.textContent = `Showing ${shown} of ${totalStatusCards} jobs`;
      }
    } else {
      jobCountEl.style.display = 'none';
      jobCountEl.textContent = '';
    }
  }

  document.querySelectorAll('.date-section-header').forEach(header => {
    const dateStr = header.dataset.dateHeader;
    if (!dateStr) return;
    const sectionCards = document.querySelectorAll(`.job-card[data-archived-at="${dateStr}"]`);
    let sectionVisibleCount = 0;
    sectionCards.forEach(c => {
      if (!c.classList.contains('hidden')) sectionVisibleCount++;
    });
    header.style.display = sectionVisibleCount > 0 ? '' : 'none';
  });

  // Keep main search bar container visible if there are jobs or user typed search text
  const searchBarContainer = document.querySelector('.main-search-bar-container');
  if (searchBarContainer) {
    searchBarContainer.style.display = (totalStatusCards > 0 || searchText.length > 0) ? 'flex' : 'none';
  }

  // Toggle profile empty state vs search results empty state
  const emptyStateEl = document.getElementById('no-jobs-empty-state');
  const searchResultsEmptyEl = document.getElementById('no-search-results-card');

  const skGrid = document.getElementById('skeleton-grid-container');
  const isSkeletonVisible = skGrid && skGrid.style.display !== 'none';

  const isMatchingRunningTab = isRunnerActive && runningSearchProfileId && (runningSearchProfileId === 'all' || selectedSearchProfile === runningSearchProfileId);

  if (shown === 0) {
    if (isMatchingRunningTab) {
      if (skGrid) skGrid.style.display = 'flex';
      const jobGrid = document.getElementById('job-grid-container');
      if (jobGrid) jobGrid.style.display = 'none';
      if (emptyStateEl) emptyStateEl.style.display = 'none';
      if (searchResultsEmptyEl) searchResultsEmptyEl.style.display = 'none';
    } else {
      if (skGrid) skGrid.style.display = 'none';
      if (totalStatusCards === 0) {
        if (emptyStateEl) emptyStateEl.style.display = 'block';
        if (searchResultsEmptyEl) searchResultsEmptyEl.style.display = 'none';
      } else {
        if (emptyStateEl) emptyStateEl.style.display = 'none';
        if (searchResultsEmptyEl) {
          searchResultsEmptyEl.style.display = 'block';
          const titleEl = document.getElementById('no-search-results-title');
          const subEl = document.getElementById('no-search-results-sub');
          if (searchText) {
            if (titleEl) titleEl.textContent = `No jobs match "${searchText}"`;
            if (subEl) subEl.textContent = 'Try checking for typos or searching with a different keyword.';
          } else {
            if (titleEl) titleEl.textContent = 'No jobs match selected filters';
            if (subEl) subEl.textContent = 'Try adjusting or clearing your active filters in the sidebar.';
          }
        }
      }
    }
  } else {
    if (skGrid) skGrid.style.display = 'none';
    const jobGrid = document.getElementById('job-grid-container');
    if (jobGrid) jobGrid.style.display = 'flex';
    if (emptyStateEl) emptyStateEl.style.display = 'none';
    if (searchResultsEmptyEl) searchResultsEmptyEl.style.display = 'none';
  }
  updateRunnerBannerVisibility();

  // Hide Archive All button if no jobs shown, or if in Archived status tab
  const archiveAllBtn = document.getElementById('header-archive-all-btn');
  if (archiveAllBtn) {
    const isArchivedStatus = (statusFilter === 'archived');
    const hasSearchProfile = (selectedSearchProfile && selectedSearchProfile !== 'all');
    const shouldShowArchiveAll = !isArchivedStatus && hasSearchProfile && (shown > 0);
    archiveAllBtn.style.display = shouldShowArchiveAll ? 'inline-flex' : 'none';
  }

  sortAndReorderCardsInDOM();
  updateClearAllFiltersVisibility();
}

function updateClearAllFiltersVisibility() {
  const btn = document.getElementById('clear-all-filters-btn');
  const wrapper = document.getElementById('clear-all-wrapper');
  if (!btn) return;

  const searchInput = document.getElementById('main-search-input');
  const hasSearchText = searchInput && searchInput.value.trim() !== '';

  const hasStatusFilter = statusFilter !== 'all';

  const checkedFilters = document.querySelectorAll('.filter-checkbox-input:checked');
  let hasCategoryFilter = false;
  checkedFilters.forEach(cb => {
    if (cb.dataset.filterVal !== 'all') {
      hasCategoryFilter = true;
    }
  });

  const isFilterActive = hasSearchText || hasStatusFilter || hasCategoryFilter;
  btn.style.display = isFilterActive ? 'block' : 'none';
  if (wrapper) wrapper.style.display = isFilterActive ? 'block' : 'none';
}

let sortField = 'score';
let sortDirection = 'desc';

const SORT_FIELD_LABELS = {
  'score': 'Relevance',
  'date': 'Date',
  'company': 'Company',
  'title': 'Title'
};

function initSortDropdown() {
  const savedField = localStorage.getItem('applypilot_sort_field');
  const savedDir = localStorage.getItem('applypilot_sort_direction');

  if (savedField && SORT_FIELD_LABELS[savedField]) {
    sortField = savedField;
  }
  if (savedDir && (savedDir === 'asc' || savedDir === 'desc')) {
    sortDirection = savedDir;
  }

  updateSortDropdownUI();

  document.addEventListener('click', function(e) {
    const dropdown = document.getElementById('sort-dropdown');
    if (dropdown && !dropdown.contains(e.target)) {
      dropdown.classList.remove('open');
    }
  });
}

function toggleSortDropdown(event) {
  if (event) event.stopPropagation();
  const dropdown = document.getElementById('sort-dropdown');
  if (dropdown) {
    dropdown.classList.toggle('open');
  }
}

function selectSortField(field, labelText) {
  if (!SORT_FIELD_LABELS[field]) return;
  if (field === sortField) {
    sortDirection = (sortDirection === 'desc') ? 'asc' : 'desc';
  } else {
    sortField = field;
    sortDirection = 'desc';
  }
  localStorage.setItem('applypilot_sort_field', sortField);
  localStorage.setItem('applypilot_sort_direction', sortDirection);
  updateSortDropdownUI();

  const dropdown = document.getElementById('sort-dropdown');
  if (dropdown) dropdown.classList.remove('open');

  applyFilters();
}

function toggleSortDirectionFromTrigger(event) {
  if (event) event.stopPropagation();
  sortDirection = (sortDirection === 'desc') ? 'asc' : 'desc';
  localStorage.setItem('applypilot_sort_direction', sortDirection);
  updateSortDropdownUI();
  applyFilters();
}

function updateSortDropdownUI() {
  const labelEl = document.getElementById('sort-current-label');
  if (labelEl) {
    labelEl.textContent = SORT_FIELD_LABELS[sortField] || 'Relevance';
  }

  const dirIcon = document.getElementById('sort-dir-icon');
  if (dirIcon) {
    if (sortDirection === 'desc') {
      dirIcon.setAttribute('title', 'Sort Order: Descending');
      dirIcon.innerHTML = '<line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline>';
    } else {
      dirIcon.setAttribute('title', 'Sort Order: Ascending');
      dirIcon.innerHTML = '<line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline>';
    }
  }

  document.querySelectorAll('.sort-dropdown-item').forEach(item => {
    const fieldKey = item.dataset.sortField;
    const itemDirIcon = item.querySelector('.item-dir-icon');
    if (fieldKey === sortField) {
      item.classList.add('active');
      if (itemDirIcon) {
        if (sortDirection === 'desc') {
          itemDirIcon.innerHTML = '<line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline>';
        } else {
          itemDirIcon.innerHTML = '<line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline>';
        }
      }
    } else {
      item.classList.remove('active');
    }
  });
}

function sortAndReorderCardsInDOM() {
  const container = document.getElementById('job-grid-container');
  if (!container) return;

  const cards = Array.from(container.children).filter(el => el.classList.contains('job-card'));
  if (cards.length === 0) return;

  const isDesc = (sortDirection === 'desc');

  cards.sort((a, b) => {
    // 1. Expired jobs always sort to the bottom
    const expiredA = parseInt(a.dataset.isExpired || '0', 10);
    const expiredB = parseInt(b.dataset.isExpired || '0', 10);
    if (expiredA !== expiredB) {
      return expiredA - expiredB;
    }

    // 2. Reposted ghost jobs (repostCount >= 2) rank lower than fresh jobs
    const repostA = parseInt(a.dataset.repostCount || '1', 10) >= 2 ? 1 : 0;
    const repostB = parseInt(b.dataset.repostCount || '1', 10) >= 2 ? 1 : 0;
    if (repostA !== repostB) {
      return repostA - repostB;
    }

    let valA, valB;

    if (sortField === 'score') {
      valA = parseFloat(a.dataset.score || '0');
      valB = parseFloat(b.dataset.score || '0');
      return isDesc ? (valB - valA) : (valA - valB);
    }

    if (sortField === 'date') {
      valA = a.dataset.date || a.dataset.postedRelative || '';
      valB = b.dataset.date || b.dataset.postedRelative || '';
      return isDesc ? valB.localeCompare(valA) : valA.localeCompare(valB);
    }

    if (sortField === 'company') {
      valA = (a.dataset.company || '').toLowerCase();
      valB = (b.dataset.company || '').toLowerCase();
      return isDesc ? valB.localeCompare(valA) : valA.localeCompare(valB);
    }

    if (sortField === 'title') {
      const titleElA = a.querySelector('.job-title');
      const titleElB = b.querySelector('.job-title');
      valA = (a.dataset.title || (titleElA ? titleElA.textContent : '')).toLowerCase();
      valB = (b.dataset.title || (titleElB ? titleElB.textContent : '')).toLowerCase();
      return isDesc ? valB.localeCompare(valA) : valA.localeCompare(valB);
    }

    return 0;
  });

  cards.forEach(card => container.appendChild(card));
}

function restoreFilterButtonsUI() {
  const syncGroup = (type, targetSet) => {
    const items = document.querySelectorAll(`.filter-checkbox-item[data-filter-type="${type}"]`);
    const isAllActive = (targetSet.size === 0);

    items.forEach(item => {
      const input = item.querySelector('.filter-checkbox-input');
      const val = (item.dataset.filterVal || '').toLowerCase();
      const isActive = (val === 'all') ? isAllActive : targetSet.has(val);

      item.classList.toggle('active', isActive);
      if (input) input.checked = isActive;
    });
  };

  syncGroup('scores', selectedScores);
  syncGroup('sources', selectedSources);
  syncGroup('countries', selectedCountries);
  syncGroup('workmodes', selectedWorkModes);
  syncGroup('apptypes', selectedAppTypes);
  syncGroup('companies', selectedCompanies);
}

// Slide-out Copilot Drawer Functions
let activeDrawerCard = null;

function handleCardClick(event, card) {
  if (event.target.closest('button, a, input, label, select')) {
    return;
  }
  openJobDrawer(card);
}

function syncDrawerActionButtons() {
  if (!activeDrawerCard) return;
  const url = activeDrawerCard.dataset.jobUrl;
  const applyUrl = activeDrawerCard.dataset.applyUrl || url;
  const isEasyApply = activeDrawerCard.dataset.isEasyApply === 'true';
  const siteVal = (activeDrawerCard.dataset.site || '').toLowerCase();
  const isLinkedInEasy = isEasyApply && siteVal === 'linkedin';
  const isSaved = activeDrawerCard.dataset.saved === 'true';
  const isApplied = activeDrawerCard.dataset.status === 'applied';
  const isArchived = activeDrawerCard.dataset.archived === 'true';
  const appliedAt = activeDrawerCard.dataset.appliedAt;

  const btnApply = document.getElementById('drawer-btn-apply');
  if (btnApply) {
    btnApply.href = applyUrl || '#';
    if (isLinkedInEasy) {
      btnApply.innerHTML = 'Easy Apply &rarr;';
      btnApply.classList.add('btn-easy-apply');
    } else {
      btnApply.innerHTML = 'Apply Now &rarr;';
      btnApply.classList.remove('btn-easy-apply');
    }
  }

  const btnSave = document.getElementById('drawer-btn-save');
  if (btnSave) {
    btnSave.textContent = isSaved ? 'Saved' : 'Save';
    btnSave.classList.toggle('active', isSaved);
  }

  const btnApplied = document.getElementById('drawer-btn-applied');
  if (btnApplied) {
    if (isApplied) {
      const formattedDate = formatAppliedDateShort(appliedAt);
      btnApplied.textContent = `Applied on ${formattedDate}`;
    } else {
      btnApplied.textContent = 'Mark as Applied';
    }
    btnApplied.classList.toggle('active', isApplied);
  }

  const btnArchive = document.getElementById('drawer-btn-archive');
  if (btnArchive) {
    btnArchive.textContent = isArchived ? 'Archived' : 'Archive';
    btnArchive.classList.toggle('active', isArchived);
  }
}

function openJobDrawer(card) {
  if (!card) return;
  activeDrawerCard = card;
  switchDrawerTab('details');

  const title = card.querySelector('.job-title') ? card.querySelector('.job-title').textContent : 'Job Details';
  const company = card.querySelector('.company-name') ? card.querySelector('.company-name').textContent : '';
  const scorePill = card.querySelector('.score-pill');
  const metaTags = card.querySelector('.meta-tags');
  const keywordsRow = card.querySelector('.keywords-row');
  const reasoningRow = card.querySelector('.reasoning-row');
  const fullDescData = card.querySelector('.full-desc-data');
  const assetLinks = card.querySelector('.asset-links');

  const titleEl = document.getElementById('drawer-job-title');
  if (titleEl) titleEl.textContent = title;

  const companyEl = document.getElementById('drawer-job-company');
  if (companyEl) companyEl.textContent = company;

  const logoImg = card.querySelector('.company-avatar-img');
  const logoAvatar = card.querySelector('.company-avatar');
  const drawerLogoBox = document.getElementById('drawer-company-logo');
  if (drawerLogoBox) {
    if (logoImg) {
      drawerLogoBox.innerHTML = logoImg.outerHTML;
    } else if (logoAvatar) {
      drawerLogoBox.innerHTML = logoAvatar.outerHTML;
    } else {
      drawerLogoBox.innerHTML = '';
    }
  }

  const scoreEl = document.getElementById('drawer-job-score');
  const rawScore = parseInt(card.dataset.score || '0', 10);
  if (scoreEl) {
    if (scorePill && scorePill.textContent.trim()) {
      scoreEl.className = scorePill.className;
      scoreEl.textContent = scorePill.textContent;
      scoreEl.style.display = 'inline-block';
    } else if (rawScore > 0) {
      let scoreCls = 'score-low';
      let displayScore = rawScore > 10 ? rawScore : rawScore * 10;
      let badgeText = `Match Score: ${displayScore}%`;
      if (rawScore >= 85 || (rawScore >= 8 && rawScore <= 10)) {
        scoreCls = 'score-high';
      } else if (rawScore >= 70 || rawScore === 7) {
        scoreCls = 'score-solid';
      } else if (rawScore >= 50 || rawScore === 5 || rawScore === 6) {
        scoreCls = 'score-moderate';
      }
      scoreEl.className = `score-pill ${scoreCls}`;
      scoreEl.textContent = badgeText;
      scoreEl.style.display = 'inline-block';
    } else {
      scoreEl.style.display = 'none';
      scoreEl.textContent = '';
    }
  }

  const metaEl = document.getElementById('drawer-meta-tags');
  if (metaEl && metaTags) {
    metaEl.innerHTML = metaTags.innerHTML;
  }

  const postedTimeTag = card.querySelector('.posted-time-tag');
  const drawerPostedEl = document.getElementById('drawer-job-posted-time');
  if (drawerPostedEl) {
    if (postedTimeTag && postedTimeTag.textContent.trim()) {
      drawerPostedEl.textContent = postedTimeTag.textContent;
      drawerPostedEl.style.display = 'inline-block';
    } else if (card.dataset.postedRelative) {
      drawerPostedEl.textContent = card.dataset.postedRelative;
      drawerPostedEl.style.display = 'inline-block';
    } else {
      drawerPostedEl.style.display = 'none';
      drawerPostedEl.textContent = '';
    }
  }

  const kwEl = document.getElementById('drawer-keywords-row');
  if (kwEl) {
    if (keywordsRow) {
      kwEl.textContent = keywordsRow.textContent;
      kwEl.style.display = 'block';
    } else {
      kwEl.style.display = 'none';
    }
  }

  const reEl = document.getElementById('drawer-reasoning-row');
  if (reEl) {
    if (reasoningRow) {
      reEl.textContent = reasoningRow.textContent;
      reEl.style.display = 'block';
    } else {
      reEl.style.display = 'none';
    }
  }

function formatJobDescription(text) {
  if (!text) return '';

  const strText = String(text).trim();

  // If text already contains clean semantic HTML tags (<p>, <ul>, <li>, <h4>, <strong>, etc.), return directly
  if (/<(p|ul|ol|li|h[1-6]|strong|em|br|a)\b/i.test(strText)) {
    return strText;
  }

  // Clean excessive blank lines, multiple spaces & tabs
  let cleaned = strText
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  const lines = cleaned.split('\n');
  let resultHtml = [];
  let inList = false;

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];
    let trimmed = line.trim();

    if (!trimmed) {
      if (inList) {
        resultHtml.push('</ul>');
        inList = false;
      }
      resultHtml.push('<div style="height:0.35rem;"></div>');
      continue;
    }

    // Check for bullet items: * item, - item, • item
    if (/^[\*\-\•]\s+/.test(trimmed)) {
      if (!inList) {
        resultHtml.push('<ul style="margin:0.35rem 0 0.5rem 1.25rem; padding:0; list-style-type:disc;">');
        inList = true;
      }
      const rawContent = trimmed.replace(/^[\*\-\•]\s+/, '');
      let escapedContent = escapeHtml(rawContent);
      escapedContent = escapedContent.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      escapedContent = escapedContent.replace(/__(.*?)__/g, '<strong>$1</strong>');
      resultHtml.push(`<li style="margin-bottom:0.25rem; line-height:1.5;">${escapedContent}</li>`);
      continue;
    } else {
      if (inList) {
        resultHtml.push('</ul>');
        inList = false;
      }
    }

    // Check for headers (e.g. # Header or ### Header)
    if (/^#{1,4}\s+/.test(trimmed)) {
      const headerText = escapeHtml(trimmed.replace(/^#{1,4}\s+/, '')).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      resultHtml.push(`<h4 style="font-size:0.95rem; font-weight:700; color:var(--text-primary); margin:0.75rem 0 0.35rem 0;">${headerText}</h4>`);
      continue;
    }

    // Regular line with bold formatting
    let formattedText = escapeHtml(trimmed);
    formattedText = formattedText.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    formattedText = formattedText.replace(/__(.*?)__/g, '<strong>$1</strong>');

    resultHtml.push(`<p style="margin:0 0 0.35rem 0; line-height:1.55;">${formattedText}</p>`);
  }

  if (inList) {
    resultHtml.push('</ul>');
  }

  return resultHtml.join('\n');
}

  const descEl = document.getElementById('drawer-full-description');
  if (descEl) {
    const rawDesc = fullDescData ? (fullDescData.innerHTML || fullDescData.textContent) : '';
    descEl.innerHTML = formatJobDescription(rawDesc);
  }

  const assetsEl = document.getElementById('drawer-asset-links');
  if (assetsEl) {
    assetsEl.innerHTML = assetLinks ? assetLinks.innerHTML : '';
  }

  // Populate Notes in Drawer
  const notesContainer = card.querySelector('.job-notes-data');
  const notesVal = notesContainer ? notesContainer.textContent : (card.dataset.notes || '');
  const notesInput = document.getElementById('drawer-crm-notes-input');
  if (notesInput) notesInput.value = notesVal;

  const notesStatusEl = document.getElementById('drawer-notes-status');
  if (notesStatusEl) notesStatusEl.textContent = '';

  syncDrawerActionButtons();

  const overlay = document.getElementById('drawer-overlay');
  const drawer = document.getElementById('copilot-drawer');
  if (overlay) overlay.classList.add('active');
  if (drawer) drawer.classList.add('active');
}

let notesDebounceTimer = null;

function handleNotesInput() {
  const statusEl = document.getElementById('drawer-notes-status');
  if (statusEl) statusEl.textContent = 'Unsaved changes...';
  if (notesDebounceTimer) clearTimeout(notesDebounceTimer);
  notesDebounceTimer = setTimeout(() => {
    saveJobNotesFromDrawer(true);
  }, 1200);
}

function saveJobNotesFromDrawer(silent = false) {
  if (!activeDrawerCard) return;
  const url = activeDrawerCard.dataset.jobUrl;
  const notesInput = document.getElementById('drawer-crm-notes-input');
  const notesVal = notesInput ? notesInput.value : '';
  const statusEl = document.getElementById('drawer-notes-status');

  if (statusEl && !silent) statusEl.textContent = 'Saving notes...';

  fetch('/api/job/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: url, notes: notesVal })
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      if (statusEl) statusEl.textContent = 'Notes saved';
      activeDrawerCard.dataset.notes = notesVal;
      let notesContainer = activeDrawerCard.querySelector('.job-notes-data');
      if (!notesContainer) {
        notesContainer = document.createElement('div');
        notesContainer.className = 'job-notes-data';
        notesContainer.style.display = 'none';
        const leftContent = activeDrawerCard.querySelector('.card-left-content');
        if (leftContent) leftContent.appendChild(notesContainer);
      }
      if (notesContainer) notesContainer.textContent = notesVal;

      let metaTags = activeDrawerCard.querySelector('.meta-tags');
      let existingBadge = metaTags ? metaTags.querySelector('.notes-badge') : null;
      if (notesVal.trim()) {
        if (!existingBadge && metaTags) {
          const badge = document.createElement('span');
          badge.className = 'meta-tag notes-badge';
          badge.style.cssText = 'background:#fef3c7;color:#92400e;border:1px solid #fde68a;';
          badge.title = notesVal.slice(0, 80);
          badge.textContent = 'Notes';
          metaTags.appendChild(badge);
        } else if (existingBadge) {
          existingBadge.title = notesVal.slice(0, 80);
        }
      } else if (existingBadge) {
        existingBadge.remove();
      }
      if (!silent) showToast('Notes saved successfully');
    }
  })
  .catch(err => {
    if (statusEl) statusEl.textContent = 'Error saving notes';
  });
}

window.handleNotesInput = handleNotesInput;
window.saveJobNotesFromDrawer = saveJobNotesFromDrawer;

function openCopilotDrawer(url) {
  const card = document.querySelector(`.job-card[data-job-url="${url}"]`);
  if (card) {
    openJobDrawer(card);
  }
}

function triggerDrawerSave() {
  if (!activeDrawerCard) return;
  const btn = activeDrawerCard.querySelector('.btn-saved');
  const url = activeDrawerCard.dataset.jobUrl;
  if (btn) {
    toggleSaveJob(btn, url);
  }
  syncDrawerActionButtons();
}

function triggerDrawerApplied() {
  if (!activeDrawerCard) return;
  const btn = activeDrawerCard.querySelector('.btn-applied');
  const url = activeDrawerCard.dataset.jobUrl;
  if (btn) {
    toggleAppliedJob(btn, url);
  }
  syncDrawerActionButtons();
}

function triggerDrawerArchive() {
  if (!activeDrawerCard) return;
  const btn = activeDrawerCard.querySelector('.btn-archive');
  const url = activeDrawerCard.dataset.jobUrl;
  if (btn) {
    toggleArchiveJob(btn, url);
  }
  syncDrawerActionButtons();
}

function closeCopilotDrawer() {
  const overlay = document.getElementById('drawer-overlay');
  const drawer = document.getElementById('copilot-drawer');
  if (overlay) overlay.classList.remove('active');
  if (drawer) drawer.classList.remove('active');
}

function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const originalText = btn.textContent;
    btn.textContent = 'Copied!';
    btn.style.background = '#22c55e';
    btn.style.color = '#ffffff';
    setTimeout(() => {
      btn.textContent = originalText;
      btn.style.background = '';
      btn.style.color = '';
    }, 1500);
  });
}

// Drag and Drop Tab Reordering Logic
let draggedTabId = null;

function handleTabDragStart(e) {
  const btn = e.currentTarget;
  draggedTabId = btn.dataset.profileId;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', draggedTabId);
  btn.classList.add('tab-dragging');
}

function handleTabDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}

function handleTabDragEnter(e) {
  e.preventDefault();
  const btn = e.currentTarget;
  if (btn.dataset.profileId && btn.dataset.profileId !== draggedTabId) {
    btn.classList.add('tab-drag-over');
  }
}

function handleTabDragLeave(e) {
  const btn = e.currentTarget;
  btn.classList.remove('tab-drag-over');
}

function handleTabDrop(e) {
  e.preventDefault();
  const dropBtn = e.currentTarget;
  dropBtn.classList.remove('tab-drag-over');

  const targetProfileId = dropBtn.dataset.profileId;
  if (!draggedTabId || !targetProfileId || draggedTabId === targetProfileId) {
    return;
  }

  let profiles = window.allSearchProfiles || allSearchProfiles || [];
  const fromIdx = profiles.findIndex(p => (p.id || 'search_1') === draggedTabId);
  const toIdx = profiles.findIndex(p => (p.id || 'search_1') === targetProfileId);

  if (fromIdx !== -1 && toIdx !== -1) {
    const movedItem = profiles.splice(fromIdx, 1)[0];
    profiles.splice(toIdx, 0, movedItem);

    window.allSearchProfiles = profiles;
    allSearchProfiles = profiles;

    const newOrder = profiles.map(p => p.id || 'search_1');
    try {
      localStorage.setItem('applypilot_tab_order', JSON.stringify(newOrder));
    } catch(err) {}

    fetch('/api/searches/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order: newOrder }),
    }).catch(err => console.error("Error saving tab order to server:", err));

    renderTopSearchTabs(profiles);
  }
}

function handleTabDragEnd(e) {
  document.querySelectorAll('.search-tab-btn').forEach(btn => {
    btn.classList.remove('tab-dragging');
    btn.classList.remove('tab-drag-over');
  });
  draggedTabId = null;
}

// Modal Management Functions
function renderTopSearchTabs(profiles) {
  let list = profiles || window.allSearchProfiles || allSearchProfiles || [];
  
  try {
    const savedOrder = JSON.parse(localStorage.getItem('applypilot_tab_order') || 'null');
    if (savedOrder && Array.isArray(savedOrder) && savedOrder.length > 0) {
      const orderMap = new Map();
      savedOrder.forEach((id, idx) => orderMap.set(id, idx));
      list = [...list].sort((a, b) => {
        const idA = a.id || 'search_1';
        const idB = b.id || 'search_1';
        const idxA = orderMap.has(idA) ? orderMap.get(idA) : 999;
        const idxB = orderMap.has(idB) ? orderMap.get(idB) : 999;
        return idxA - idxB;
      });
    }
  } catch(e) {}

  window.allSearchProfiles = list;
  allSearchProfiles = list;

  const container = document.getElementById('search-tabs-bar');
  if (!container) return;

  const firstProfileId = (list && list.length > 0) ? (list[0].id || 'search_1') : 'search_1';
  if (selectedSearchProfile === 'all') {
    selectedSearchProfile = firstProfileId;
  }

  let html = '';
  (list || []).forEach((sp, idx) => {
    const spId = sp.id || `search_${idx + 1}`;
    const spName = safeDecodeText(sp.name || 'Search Profile');
    const activeCls = (selectedSearchProfile === spId || (idx === 0 && !selectedSearchProfile)) ? 'active' : '';
    html += `<button class="search-tab-btn ${activeCls}" data-profile-id="${spId}" draggable="true" ondragstart="handleTabDragStart(event)" ondragover="handleTabDragOver(event)" ondragenter="handleTabDragEnter(event)" ondragleave="handleTabDragLeave(event)" ondrop="handleTabDrop(event)" ondragend="handleTabDragEnd(event)" onclick="filterSearchProfile('${spId}')">${escapeHtml(spName)}</button>`;
  });
  html += `<button class="search-tab-btn new-search-tab" draggable="false" onclick="openEditProfileForm(false)">+ New Search</button>`;
  container.innerHTML = html;
  updateSearchTabsRunningState(runningSearchProfileId, isRunnerActive);
  updateSearchTabBadges();
}

function openSearchManagerModal() {
  triggerEditCurrentSearch();
}

function closeSearchManagerModal() {
  const modal = document.getElementById('search-manager-modal');
  if (modal) modal.style.display = 'none';
}

function handleCancelSearchForm() {
  closeSearchManagerModal();
}

function handleDeleteSearchFromForm() {
  const searchId = document.getElementById('form-search-id').value;
  if (!searchId) return;
  deleteSearchProfile(searchId);
}

function openEditProfileForm() {
  document.getElementById('search-manager-modal').style.display = 'flex';

  const deleteBtn = document.getElementById('form-delete-search-btn');
  if (deleteBtn) deleteBtn.style.display = 'none';

  document.getElementById('form-search-id').value = 'search_' + Date.now();
  document.getElementById('form-search-name').value = '';
  document.getElementById('form-search-queries').value = '';
  document.getElementById('form-search-locations').value = '';
  document.getElementById('form-search-types').value = 'Any';
  document.getElementById('form-search-country').value = 'worldwide';

  document.getElementById('form-site-linkedin').checked = true;
  document.getElementById('form-site-indeed').checked = true;
  if (document.getElementById('form-site-getonboard')) document.getElementById('form-site-getonboard').checked = true;
  if (document.getElementById('form-site-remotive')) document.getElementById('form-site-remotive').checked = true;
  if (document.getElementById('form-site-weworkremotely')) document.getElementById('form-site-weworkremotely').checked = true;
  if (document.getElementById('form-site-hirewithnear')) document.getElementById('form-site-hirewithnear').checked = true;

  document.getElementById('form-search-results-per-site').value = '50';
  document.getElementById('form-search-hours-old').value = '24';
  document.getElementById('form-search-easy-apply').checked = false;

  document.getElementById('form-search-min-salary').value = '';
  document.getElementById('form-search-experience-level').value = 'mid_senior';

  const fileInput = document.getElementById('form-search-pdf-input');
  if (fileInput) fileInput.value = '';
  const badge = document.getElementById('form-pdf-status-badge');
  if (badge) badge.textContent = '';
  const viewLink = document.getElementById('form-pdf-view-link');
  if (viewLink) viewLink.style.display = 'none';

  if (document.getElementById('form-search-blocked-posters')) {
    document.getElementById('form-search-blocked-posters').value = '';
  }

  if (document.getElementById('form-score-high')) document.getElementById('form-score-high').checked = true;
  if (document.getElementById('form-score-solid')) document.getElementById('form-score-solid').checked = true;
  if (document.getElementById('form-score-moderate')) document.getElementById('form-score-moderate').checked = true;
  if (document.getElementById('form-score-low')) document.getElementById('form-score-low').checked = false;

  document.getElementById('form-title').textContent = 'Create New Search Profile';
  document.getElementById('modal-search-form').style.display = 'flex';
}

function editSearchProfile(spOrId) {
  document.getElementById('search-manager-modal').style.display = 'flex';

  const profilesList = window.allSearchProfiles || allSearchProfiles || [];
  const deleteBtn = document.getElementById('form-delete-search-btn');
  if (deleteBtn) {
    deleteBtn.style.display = (profilesList.length > 1) ? 'inline-block' : 'none';
  }

  const sp = (typeof spOrId === 'string')
    ? ((profilesList).find(p => p.id === spOrId) || profilesList[0] || {})
    : (spOrId || profilesList[0] || {});

  document.getElementById('form-search-id').value = sp.id || ('search_' + Date.now());
  document.getElementById('form-search-name').value = safeDecodeText(sp.name || '');
  document.getElementById('form-search-queries').value = Array.isArray(sp.queries) ? sp.queries.map(safeDecodeText).join(', ') : safeDecodeText(sp.queries || '');
  document.getElementById('form-search-locations').value = Array.isArray(sp.locations) ? sp.locations.map(safeDecodeText).join(', ') : safeDecodeText(sp.locations || '');
  
  const rawTypes = Array.isArray(sp.job_types) ? sp.job_types[0] : (sp.job_types || sp.types || 'Any');
  const tLower = (rawTypes || '').toString().toLowerCase().trim();
  const selectTypeEl = document.getElementById('form-search-types');
  if (selectTypeEl) {
    if (tLower.includes('remote')) selectTypeEl.value = 'Remote';
    else if (tLower.includes('hybrid')) selectTypeEl.value = 'Hybrid';
    else if (tLower.includes('person') || tLower.includes('site') || tLower.includes('in-person')) selectTypeEl.value = 'In-Person';
    else selectTypeEl.value = 'Any';
  }

  document.getElementById('form-search-country').value = sp.country || 'worldwide';

  const sites = Array.isArray(sp.sites) ? sp.sites : ["linkedin", "indeed", "getonboard", "remotive", "weworkremotely", "hirewithnear"];
  if (document.getElementById('form-site-linkedin')) document.getElementById('form-site-linkedin').checked = sites.includes('linkedin');
  if (document.getElementById('form-site-indeed')) document.getElementById('form-site-indeed').checked = sites.includes('indeed');
  if (document.getElementById('form-site-getonboard')) document.getElementById('form-site-getonboard').checked = sites.includes('getonboard') || sites.includes('get_on_board');
  if (document.getElementById('form-site-remotive')) document.getElementById('form-site-remotive').checked = sites.includes('remotive');
  if (document.getElementById('form-site-weworkremotely')) document.getElementById('form-site-weworkremotely').checked = sites.includes('weworkremotely');
  if (document.getElementById('form-site-hirewithnear')) document.getElementById('form-site-hirewithnear').checked = sites.includes('hirewithnear') || sites.includes('hire_with_near') || sites.includes('near');

  document.getElementById('form-search-results-per-site').value = sp.results_per_site || '50';
  document.getElementById('form-search-hours-old').value = sp.hours_old || '24';
  document.getElementById('form-search-easy-apply').checked = !!sp.easy_apply_only;

  document.getElementById('form-search-min-salary').value = sp.min_salary || '';
  document.getElementById('form-search-experience-level').value = sp.experience_level || 'mid_senior';

  const cand = sp.candidate || sp.profile || {};
  if (document.getElementById('form-candidate-name')) document.getElementById('form-candidate-name').value = cand.name || cand.full_name || '';
  if (document.getElementById('form-candidate-title')) document.getElementById('form-candidate-title').value = cand.title || cand.target_role || '';
  if (document.getElementById('form-candidate-email')) document.getElementById('form-candidate-email').value = cand.email || '';
  if (document.getElementById('form-candidate-phone')) document.getElementById('form-candidate-phone').value = cand.phone || '';
  if (document.getElementById('form-candidate-skills')) document.getElementById('form-candidate-skills').value = Array.isArray(cand.skills) ? cand.skills.join(', ') : (cand.skills || '');
  if (document.getElementById('form-candidate-summary')) document.getElementById('form-candidate-summary').value = cand.summary || '';

  const fileInput = document.getElementById('form-search-pdf-input');
  if (fileInput) fileInput.value = '';

  const badge = document.getElementById('form-pdf-status-badge');
  const viewLink = document.getElementById('form-pdf-view-link');

  if (document.getElementById('form-search-blocked-posters')) {
    const bp = sp.blocked_posters;
    document.getElementById('form-search-blocked-posters').value = Array.isArray(bp) ? bp.join(', ') : (bp || '');
  }

  const scoreFilters = Array.isArray(sp.score_filters) && sp.score_filters.length > 0
    ? sp.score_filters.map(s => String(s).toLowerCase().trim())
    : ['high', 'solid', 'moderate'];
  if (document.getElementById('form-score-high')) document.getElementById('form-score-high').checked = scoreFilters.includes('high');
  if (document.getElementById('form-score-solid')) document.getElementById('form-score-solid').checked = scoreFilters.includes('solid');
  if (document.getElementById('form-score-moderate')) document.getElementById('form-score-moderate').checked = scoreFilters.includes('moderate');
  if (document.getElementById('form-score-low')) document.getElementById('form-score-low').checked = scoreFilters.includes('low');

  if (sp.has_pdf_resume) {
    if (badge) badge.textContent = 'Attached';
    if (viewLink) {
      viewLink.href = sp.pdf_url;
      viewLink.style.display = 'inline-flex';
    }
  } else {
    if (badge) badge.textContent = 'Default resume';
    if (viewLink) viewLink.style.display = 'none';
  }

  document.getElementById('form-title').textContent = 'Edit Search Profile';
  document.getElementById('modal-search-form').style.display = 'flex';
}

function closeEditProfileForm() {
  closeSearchManagerModal();
}

function saveSearchProfileFromModal() {
  const sid = document.getElementById('form-search-id').value;
  const name = document.getElementById('form-search-name').value.trim();
  const qStr = document.getElementById('form-search-queries').value.trim();
  const lStr = document.getElementById('form-search-locations').value.trim();
  const selectedType = document.getElementById('form-search-types').value || 'Any';
  const country = document.getElementById('form-search-country').value.trim() || 'worldwide';

  if (!name || !qStr) {
    alert("Please provide a search name and at least one query keyword.");
    return;
  }

  const queries = qStr.split(',').map(s => s.trim()).filter(Boolean);
  const locations = lStr.split(',').map(s => s.trim()).filter(Boolean);
  const job_types = (selectedType && selectedType !== 'Any') ? [selectedType] : [];

  const sites = [];
  if (document.getElementById('form-site-linkedin') && document.getElementById('form-site-linkedin').checked) sites.push('linkedin');
  if (document.getElementById('form-site-indeed') && document.getElementById('form-site-indeed').checked) sites.push('indeed');
  if (document.getElementById('form-site-getonboard') && document.getElementById('form-site-getonboard').checked) sites.push('getonboard');
  if (document.getElementById('form-site-remotive') && document.getElementById('form-site-remotive').checked) sites.push('remotive');
  if (document.getElementById('form-site-weworkremotely') && document.getElementById('form-site-weworkremotely').checked) sites.push('weworkremotely');
  if (document.getElementById('form-site-hirewithnear') && document.getElementById('form-site-hirewithnear').checked) sites.push('hirewithnear');

  const results_per_site = parseInt(document.getElementById('form-search-results-per-site').value) || 50;
  const hours_old = parseInt(document.getElementById('form-search-hours-old').value) || 24;
  const easy_apply_only = document.getElementById('form-search-easy-apply').checked;

  const min_salary = document.getElementById('form-search-min-salary').value.trim();
  const experience_level = document.getElementById('form-search-experience-level').value;

  const blockedPostersRaw = (document.getElementById('form-search-blocked-posters') ? document.getElementById('form-search-blocked-posters').value : '').trim();
  const blockedPosters = blockedPostersRaw ? blockedPostersRaw.split(',').map(s => s.trim()).filter(Boolean) : [];

  const candSkillsRaw = (document.getElementById('form-candidate-skills') ? document.getElementById('form-candidate-skills').value : '').trim();
  const candSkills = candSkillsRaw ? candSkillsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  const candidateObj = {
    name: (document.getElementById('form-candidate-name') ? document.getElementById('form-candidate-name').value.trim() : ''),
    title: (document.getElementById('form-candidate-title') ? document.getElementById('form-candidate-title').value.trim() : ''),
    email: (document.getElementById('form-candidate-email') ? document.getElementById('form-candidate-email').value.trim() : ''),
    phone: (document.getElementById('form-candidate-phone') ? document.getElementById('form-candidate-phone').value.trim() : ''),
    skills: candSkills,
    summary: (document.getElementById('form-candidate-summary') ? document.getElementById('form-candidate-summary').value.trim() : '')
  };

  const scoreFilters = [];
  if (document.getElementById('form-score-high') && document.getElementById('form-score-high').checked) scoreFilters.push('high');
  if (document.getElementById('form-score-solid') && document.getElementById('form-score-solid').checked) scoreFilters.push('solid');
  if (document.getElementById('form-score-moderate') && document.getElementById('form-score-moderate').checked) scoreFilters.push('moderate');
  if (document.getElementById('form-score-low') && document.getElementById('form-score-low').checked) scoreFilters.push('low');
  const finalScoreFilters = scoreFilters.length > 0 ? scoreFilters : ['high', 'solid', 'moderate'];

  const profile = {
    id: sid,
    name: name,
    enabled: true,
    queries: queries,
    locations: locations,
    job_types: job_types,
    is_remote: selectedType === 'Remote',
    country: country,
    sites: sites.length > 0 ? sites : ["linkedin", "indeed", "getonboard", "remotive", "hirewithnear"],
    results_per_site: results_per_site,
    hours_old: hours_old,
    easy_apply_only: easy_apply_only,
    blocked_posters: blockedPosters,
    score_filters: finalScoreFilters,
    min_salary: min_salary,
    experience_level: experience_level,
    candidate: candidateObj
  };

  const fileInput = document.getElementById('form-search-pdf-input');
  const pdfFile = (fileInput && fileInput.files.length > 0) ? fileInput.files[0] : null;

  fetch('/api/searches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ search: profile }),
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      if (pdfFile) {
        const reader = new FileReader();
        reader.onload = function(e) {
          const b64 = e.target.result.split(',')[1];
          fetch('/api/searches/upload-resume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id: sid,
              file_b64: b64,
              filename: pdfFile.name,
            }),
          })
          .then(r => r.json())
          .then(upData => {
            fetch('/api/searches')
              .then(res => res.json())
              .then(data => {
                const searches = data.searches || [];
                window.allSearchProfiles = searches;
                allSearchProfiles = searches;
                renderTopSearchTabs(searches);
                if (sid) {
                  filterSearchProfile(sid);
                }
                closeSearchManagerModal();
              });
          });
        };
        reader.readAsDataURL(pdfFile);
      } else {
        fetch('/api/searches')
          .then(res => res.json())
          .then(data => {
            const searches = data.searches || [];
            window.allSearchProfiles = searches;
            allSearchProfiles = searches;
            renderTopSearchTabs(searches);
            if (sid) {
              filterSearchProfile(sid);
            }
            closeSearchManagerModal();
          });
      }
    } else if (data.quota_exceeded) {
      closeSearchManagerModal();
      openProWaitlistModal('max_tabs', data.reason, data.error);
    } else {
      alert("Error saving search profile: " + (data.error || 'Unknown error'));
    }
  });
}

function deleteSearchProfile(searchId) {
  if (!confirm("Are you sure you want to delete this search profile?")) return;

  fetch('/api/searches/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: searchId }),
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      fetch('/api/searches')
        .then(res => res.json())
        .then(sData => {
          const searches = sData.searches || [];
          window.allSearchProfiles = searches;
          allSearchProfiles = searches;
          renderTopSearchTabs(searches);
          const nextId = (searches.length > 0) ? searches[0].id : null;
          if (nextId) {
            filterSearchProfile(nextId);
          }
          closeSearchManagerModal();
        });
    }
  });
}

// Action Handlers
function animateCardExit(card, callback) {
  if (!card) {
    if (callback) callback();
    return;
  }
  card.classList.add('card-archiving-fade');
  setTimeout(() => {
    if (callback) callback();
    card.classList.remove('card-archiving-fade');
  }, 350);
}

function toggleSaveJob(btn, jobUrl) {
  const card = btn.closest('.job-card') || activeDrawerCard;
  if (!card) return;
  const isSaved = card.dataset.saved === 'true';
  const newSaved = !isSaved;
  card.dataset.saved = newSaved ? 'true' : 'false';

  btn.textContent = newSaved ? 'Saved' : 'Save';
  btn.classList.toggle('active', newSaved);

  fetch('/api/job/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: jobUrl, is_saved: newSaved, saved: newSaved }),
  });

  if (activeDrawerCard && activeDrawerCard === card) {
    syncDrawerActionButtons();
  }

  const leavesView = (statusFilter === 'all' && newSaved) || (statusFilter === 'saved' && !newSaved);
  if (leavesView) {
    animateCardExit(card, () => {
      applyFilters();
    });
  } else {
    applyFilters();
  }
}

function formatAppliedDateShort(dateStr) {
  if (!dateStr) {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const d = new Date();
    return months[d.getMonth()] + " " + d.getDate();
  }
  const parts = dateStr.split('T')[0].split('-');
  if (parts.length === 3) {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const monthIdx = parseInt(parts[1], 10) - 1;
    const day = parseInt(parts[2], 10);
    if (monthIdx >= 0 && monthIdx < 12) {
      return months[monthIdx] + " " + day;
    }
  }
  return dateStr;
}

function toggleAppliedJob(btn, jobUrl) {
  const card = btn.closest('.job-card') || activeDrawerCard;
  if (!card) return;
  const isApplied = card.dataset.status === 'applied';
  const newStatus = isApplied ? 'new' : 'applied';

  card.dataset.status = newStatus;
  const todayStr = new Date().toISOString().split('T')[0];
  if (newStatus === 'applied') {
    card.dataset.appliedAt = todayStr;
    btn.textContent = `Applied on ${formatAppliedDateShort(todayStr)}`;
  } else {
    card.dataset.appliedAt = '';
    btn.textContent = 'Mark as Applied';
  }

  btn.classList.toggle('active', newStatus === 'applied');

  fetch('/api/job/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      url: jobUrl,
      status: newStatus,
      applied_at: newStatus === 'applied' ? todayStr : null
    }),
  });

  if (activeDrawerCard && activeDrawerCard === card) {
    syncDrawerActionButtons();
  }

  const leavesView = (statusFilter === 'all' && newStatus === 'applied') ||
                     (statusFilter === 'applied' && newStatus !== 'applied') ||
                     (statusFilter === 'saved' && newStatus === 'applied');
  if (leavesView) {
    animateCardExit(card, () => {
      applyFilters();
    });
  } else {
    applyFilters();
  }
}

function toggleArchiveJob(btn, jobUrl) {
  const card = btn.closest('.job-card') || activeDrawerCard;
  if (!card) return;
  const isArchived = card.dataset.archived === 'true';
  const newArchived = !isArchived;

  card.dataset.archived = newArchived ? 'true' : 'false';
  btn.classList.toggle('active', newArchived);
  btn.textContent = newArchived ? 'Archived' : 'Archive';

  fetch('/api/job/archive', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: jobUrl, is_archived: newArchived, archived: newArchived }),
  });

  if (activeDrawerCard && activeDrawerCard === card) {
    syncDrawerActionButtons();
  }

  const leavesView = (statusFilter !== 'archived' && newArchived) || (statusFilter === 'archived' && !newArchived);
  if (leavesView) {
    animateCardExit(card, () => {
      applyFilters();
    });
  } else {
    applyFilters();
  }
}

function dismissRunnerBanner() {
  const banner = document.getElementById('runner-status-banner');
  if (banner) banner.style.display = 'none';
}

function dismissApiWarning(remindLater) {
  const banner = document.getElementById('api-warning-banner');
  if (banner) banner.style.display = 'none';
  if (remindLater) {
    // Remind in 24 hours
    const expiry = Date.now() + 24 * 60 * 60 * 1000;
    localStorage.setItem('ddw_api_banner_remind_until', String(expiry));
  } else {
    localStorage.setItem('ddw_api_banner_dismissed', 'true');
  }
}
window.dismissApiWarning = dismissApiWarning;

function dismissUpdateBanner(remindLater) {
  const banner = document.getElementById('update-available-banner');
  if (banner) banner.style.display = 'none';
  if (remindLater) {
    // Remind in 24 hours
    const expiry = Date.now() + 24 * 60 * 60 * 1000;
    localStorage.setItem('ddw_update_banner_remind_until', String(expiry));
  } else {
    const ver = window._latestDdwVersion || 'dismissed';
    localStorage.setItem('ddw_update_banner_dismissed_version', ver);
  }
}
window.dismissUpdateBanner = dismissUpdateBanner;

function triggerSystemUpdate() {
  const btn = document.getElementById('btn-update-now');
  const btnText = document.getElementById('btn-update-now-text');
  if (btn) btn.disabled = true;
  if (btnText) btnText.textContent = 'Updating...';

  fetch('/api/system/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      if (btnText) btnText.textContent = 'Updated! Reloading...';
      setTimeout(() => {
        window.location.reload();
      }, 1500);
    } else {
      alert('Update Notice: ' + (data.error || 'Update failed') + '\n\nYou can manually run: git pull origin main');
      if (btn) btn.disabled = false;
      if (btnText) btnText.textContent = 'Update Now';
    }
  })
  .catch(err => {
    alert('Update Network Notice: ' + err.message + '\n\nYou can manually run: git pull origin main');
    if (btn) btn.disabled = false;
    if (btnText) btnText.textContent = 'Update Now';
  });
}
window.triggerSystemUpdate = triggerSystemUpdate;

function checkForSystemUpdates() {
  const banner = document.getElementById('update-available-banner');
  if (!banner) return;

  const remindUntil = parseInt(localStorage.getItem('ddw_update_banner_remind_until') || '0', 10);
  if (remindUntil && Date.now() < remindUntil) {
    return;
  }

  fetch('/api/system/check-update')
    .then(res => res.json())
    .then(data => {
      if (!data || !data.update_available) return;

      const dismissedVer = localStorage.getItem('ddw_update_banner_dismissed_version');
      if (dismissedVer && dismissedVer === data.latest_version) {
        return;
      }

      window._latestDdwVersion = data.latest_version;
      const verBadge = document.getElementById('update-latest-version-badge');
      if (verBadge) {
        verBadge.textContent = 'v' + data.latest_version;
      }

      const whatsNewBtn = document.getElementById('btn-update-whats-new');
      if (whatsNewBtn && data.release_url) {
        whatsNewBtn.href = data.release_url;
      }

      banner.style.display = 'flex';
    })
    .catch(() => {});
}
window.checkForSystemUpdates = checkForSystemUpdates;

// Search Runner Execution & Live Polling

function triggerEditCurrentSearch() {
  const profilesList = window.allSearchProfiles || allSearchProfiles || [];
  let targetId = selectedSearchProfile;
  if (!targetId || targetId === 'all' || !profilesList.some(p => p.id === targetId)) {
    const activeTab = document.querySelector('.search-tab-btn.active');
    if (activeTab && activeTab.dataset.profileId) {
      targetId = activeTab.dataset.profileId;
    } else if (profilesList.length > 0) {
      targetId = profilesList[0].id;
    }
  }
  if (targetId && profilesList.some(p => p.id === targetId)) {
    editSearchProfile(targetId);
  } else {
    openEditProfileForm();
  }
}

function triggerArchiveAllJobs() {
  const profileId = selectedSearchProfile;
  const currentTab = document.querySelector(`.search-tab-btn[data-profile-id="${profileId}"]`);
  let profileName = 'this search profile';
  if (currentTab) {
    const clone = currentTab.cloneNode(true);
    clone.querySelectorAll('.tab-new-badge, .tab-spinner').forEach(el => el.remove());
    profileName = clone.textContent.replace('+ New Search', '').trim() || 'this search profile';
  }

  if (!confirm(`Are you sure you want to archive all jobs in ${profileName}?`)) {
    return;
  }

  const visibleCards = Array.from(document.querySelectorAll('.job-card')).filter(card => !card.classList.contains('hidden'));
  const urlsToArchive = visibleCards.map(c => c.dataset.jobUrl).filter(Boolean);

  if (urlsToArchive.length === 0) {
    alert("No active jobs to archive.");
    return;
  }

  // Optimistic UI update: mark archived immediately so sidebar counters and tab badges drop in sync
  visibleCards.forEach(card => {
    card.classList.add('card-archiving-fade');
    card.dataset.archived = 'true';
  });
  applyFilters();

  fetch('/api/jobs/archive-all', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      search_id: profileId,
      urls: urlsToArchive
    }),
  })
  .then(res => res.json())
  .then(data => {
    setTimeout(() => {
      visibleCards.forEach(card => {
        card.classList.remove('card-archiving-fade');
      });
      applyFilters();
    }, 350);
  })
  .catch(err => {
    console.error("Error archiving all jobs:", err);
    visibleCards.forEach(card => {
      card.dataset.archived = 'false';
      card.classList.remove('card-archiving-fade');
    });
    applyFilters();
  });
}

function restoreRunSearchButton() {
  const runBtn = document.getElementById('header-run-search-btn');
  if (runBtn) {
    runBtn.disabled = false;
    runBtn.textContent = "Run Search";
    runBtn.style.opacity = '1';
    runBtn.style.cursor = 'pointer';
  }
  restoreRunAllButton();
}

function restoreRunAllButton() {
  const allBtn = document.getElementById('sidebar-run-all-btn');
  const allTxt = document.getElementById('sidebar-run-all-text');
  if (allBtn) {
    allBtn.disabled = false;
    allBtn.style.opacity = '1';
    allBtn.style.cursor = 'pointer';
    if (allTxt) {
      allTxt.textContent = "Run All";
    } else {
      allBtn.textContent = "Run All";
    }
  }
}

function setRunAllButtonSearching() {
  const allBtn = document.getElementById('sidebar-run-all-btn');
  const allTxt = document.getElementById('sidebar-run-all-text');
  if (allBtn) {
    allBtn.disabled = true;
    allBtn.style.opacity = '0.7';
    allBtn.style.cursor = 'not-allowed';
    if (allTxt) {
      allTxt.textContent = "Running All…";
    } else {
      allBtn.textContent = "Running All…";
    }
  }
  const runBtn = document.getElementById('header-run-search-btn');
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.textContent = "Running…";
    runBtn.style.opacity = '0.7';
    runBtn.style.cursor = 'not-allowed';
  }
  const emptyStateEl = document.getElementById('no-jobs-empty-state');
  if (emptyStateEl) {
    emptyStateEl.style.display = 'none';
  }
}

function setRunSearchButtonSearching() {
  const runBtn = document.getElementById('header-run-search-btn');
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.textContent = "Running…";
    runBtn.style.opacity = '0.7';
    runBtn.style.cursor = 'not-allowed';
  }
  const allBtn = document.getElementById('sidebar-run-all-btn');
  if (allBtn) {
    allBtn.disabled = true;
    allBtn.style.opacity = '0.7';
    allBtn.style.cursor = 'not-allowed';
  }
  const emptyStateEl = document.getElementById('no-jobs-empty-state');
  if (emptyStateEl) {
    emptyStateEl.style.display = 'none';
  }
}

function triggerRunAllSearches() {
  const profilesList = window.allSearchProfiles || allSearchProfiles || [];
  if (!profilesList || profilesList.length === 0) {
    showToast("No search profiles found to run");
    return;
  }

  isRunnerActive = true;
  runningSearchProfileId = 'all';
  try { localStorage.setItem('applypilot_running_search_id', 'all'); } catch(e) {}
  setRunSearchButtonSearching();
  setRunAllButtonSearching();

  const firstProfileId = profilesList[0].id || 'search_1';
  updateSearchTabsRunningState(firstProfileId, true);
  updateRunnerBannerVisibility("Starting all searches sequentially...");

  fetch('/api/search/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: 'all' }),
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      startPollingRunnerStatus();
    } else {
      restoreRunSearchButton();
      restoreRunAllButton();
      updateSearchTabsRunningState(null, false);
      isRunnerActive = false;
      runningSearchProfileId = null;
      try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
      updateRunnerBannerVisibility();
      if (data.quota_exceeded) {
        openProWaitlistModal('run_all', data.reason, data.error);
      } else {
        updateRunnerBannerVisibility("Notice: " + (data.error || 'Search already running'));
      }
    }
  })
  .catch(err => {
    restoreRunSearchButton();
    restoreRunAllButton();
    updateSearchTabsRunningState(null, false);
    isRunnerActive = false;
    runningSearchProfileId = null;
    try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
    updateRunnerBannerVisibility();
  });
}

function triggerRunSearch(searchId) {
  const profilesList = window.allSearchProfiles || allSearchProfiles || [];
  let idToRun = searchId || selectedSearchProfile;
  if (!idToRun || idToRun === 'all' || !profilesList.some(p => p.id === idToRun)) {
    const activeTab = document.querySelector('.search-tab-btn.active');
    if (activeTab && activeTab.dataset.profileId) {
      idToRun = activeTab.dataset.profileId;
    } else if (profilesList.length > 0) {
      idToRun = profilesList[0].id;
    }
  }

  isRunnerActive = true;
  runningSearchProfileId = idToRun;
  try { localStorage.setItem('applypilot_running_search_id', runningSearchProfileId); } catch(e) {}
  setRunSearchButtonSearching();
  updateSearchTabsRunningState(runningSearchProfileId, true);

  updateRunnerBannerVisibility("Starting search runner...");

  fetch('/api/search/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: idToRun }),
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      startPollingRunnerStatus();
    } else {
      restoreRunSearchButton();
      updateSearchTabsRunningState(null, false);
      isRunnerActive = false;
      runningSearchProfileId = null;
      try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
      updateRunnerBannerVisibility();
      if (data.quota_exceeded) {
        openProWaitlistModal('run_search', data.reason, data.error);
      } else {
        updateRunnerBannerVisibility("Notice: " + (data.error || 'Search already running'));
      }
    }
  })
  .catch(err => {
    restoreRunSearchButton();
    updateSearchTabsRunningState(null, false);
    isRunnerActive = false;
    runningSearchProfileId = null;
    try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
    updateRunnerBannerVisibility();
  });
}

let clientLoadedCompletedProfiles = new Set();

function refreshJobsGridFromDashboard() {
  const currentPath = window.location.pathname + window.location.search;
  fetch(currentPath, { cache: 'no-store' })
    .then(res => res.text())
    .then(html => {
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');

      // 1. Swap in all fresh job cards
      const newGrid = doc.getElementById('job-grid-container');
      const oldGrid = document.getElementById('job-grid-container');
      if (newGrid && oldGrid) {
        oldGrid.innerHTML = newGrid.innerHTML;
      }

      // 2. Synchronize navigation status counts (New, Saved, Applied, Archived)
      doc.querySelectorAll('.status-nav .nav-item').forEach(newItem => {
        const statusType = newItem.dataset.status;
        const countSpan = newItem.querySelector('.nav-count');
        if (statusType && countSpan) {
          const oldCountSpan = document.querySelector(`.status-nav .nav-item[data-status="${statusType}"] .nav-count`);
          if (oldCountSpan) {
            oldCountSpan.textContent = countSpan.textContent;
          }
        }
      });

      // 3. Re-apply client-side filters for active tab
      applyFilters();

      // 4. Update tab badges
      updateSearchTabBadges();
    })
    .catch(err => {
      console.warn("Failed to dynamically refresh jobs grid:", err);
    });
}

function startPollingRunnerStatus() {
  if (runnerInterval) clearInterval(runnerInterval);
  runnerInterval = setInterval(pollRunnerStatus, 2000);
}

function pollRunnerStatus() {
  fetch('/api/search/status')
    .then(res => res.json())
    .then(data => {
      const skGrid = document.getElementById('skeleton-grid-container');
      const jobGrid = document.getElementById('job-grid-container');
      const emptyState = document.getElementById('no-jobs-empty-state');

      // Update per-tab newly discovered counts
      if (data.tab_new_counts && typeof data.tab_new_counts === 'object' && Object.keys(data.tab_new_counts).length > 0) {
        let savedCounts = {};
        try {
          savedCounts = JSON.parse(localStorage.getItem('ddw_tab_new_counts') || '{}');
        } catch(e) {}
        Object.assign(savedCounts, data.tab_new_counts);
        try {
          localStorage.setItem('ddw_tab_new_counts', JSON.stringify(savedCounts));
        } catch(e) {}
        updateSearchTabBadges();
      }

      // Check for newly completed profiles to release tabs incrementally
      if (Array.isArray(data.completed_profiles) && data.completed_profiles.length > 0) {
        let hasNewCompleted = false;
        data.completed_profiles.forEach(pId => {
          if (!clientLoadedCompletedProfiles.has(pId)) {
            clientLoadedCompletedProfiles.add(pId);
            hasNewCompleted = true;
          }
        });
        if (hasNewCompleted) {
          refreshJobsGridFromDashboard();
        }
      }

      if (data.profile_last_runs && typeof data.profile_last_runs === 'object') {
        let changed = false;
        (window.allSearchProfiles || []).forEach(p => {
          if (p.id in data.profile_last_runs && data.profile_last_runs[p.id] !== undefined) {
            const serverLr = data.profile_last_runs[p.id];
            if (p.last_run !== serverLr) {
              p.last_run = serverLr;
              changed = true;
            }
          }
        });
        if (changed) {
          updateRightPanelHeader(selectedSearchProfile);
        }
      }

      if (['running', 'scraping', 'scoring'].includes(data.status)) {
        isRunnerActive = true;
        if (data.search_id) {
          runningSearchProfileId = data.search_id;
          try { localStorage.setItem('applypilot_running_search_id', runningSearchProfileId); } catch(e) {}
        }
        setRunSearchButtonSearching();
        if (runningSearchProfileId === 'all') {
          setRunAllButtonSearching();
        }
        updateSearchTabsRunningState(runningSearchProfileId, true);
        updateRunnerBannerVisibility(data.message || "Scanning job portals live...", data);
        applyFilters();

        if (!runnerInterval) {
          startPollingRunnerStatus();
        }
      } else if (data.status === 'idle' || data.status === 'complete') {
        restoreRunSearchButton();
        restoreRunAllButton();
        updateSearchTabsRunningState(null, false);
        if (runnerInterval) {
          clearInterval(runnerInterval);
          runnerInterval = null;
        }
        if (isRunnerActive) {
          isRunnerActive = false;
          refreshJobsGridFromDashboard();
          updateRunnerBannerVisibility("All searches complete! All tabs ready.");
          setTimeout(() => {
            runningSearchProfileId = null;
            try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
            updateRunnerBannerVisibility();
          }, 2500);
        } else {
          runningSearchProfileId = null;
          try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
          updateRunnerBannerVisibility();
        }
      } else if (data.status === 'error') {
        restoreRunSearchButton();
        restoreRunAllButton();
        updateSearchTabsRunningState(null, false);
        isRunnerActive = false;
        runningSearchProfileId = null;
        try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
        updateRunnerBannerVisibility();
        if (skGrid) skGrid.style.display = 'none';
        applyFilters();
        if (runnerInterval) {
          clearInterval(runnerInterval);
          runnerInterval = null;
        }
      }
    })
    .catch(err => {
      console.error("Runner poll failed:", err);
      restoreRunSearchButton();
      restoreRunAllButton();
      updateSearchTabsRunningState(null, false);
      isRunnerActive = false;
      runningSearchProfileId = null;
      try { localStorage.removeItem('applypilot_running_search_id'); } catch(e) {}
      updateRunnerBannerVisibility();
      if (runnerInterval) {
        clearInterval(runnerInterval);
        runnerInterval = null;
      }
    });
}

function initTheme() {
  const savedMode = localStorage.getItem('applypilot_theme_mode') || 'system';
  applyThemeMode(savedMode);

  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
      const currentMode = localStorage.getItem('applypilot_theme_mode') || 'system';
      if (currentMode === 'system') {
        applyThemeMode('system');
      }
    });
  }
}

function setThemeMode(mode) {
  if (!['system', 'light', 'dark'].includes(mode)) return;
  localStorage.setItem('applypilot_theme_mode', mode);
  applyThemeMode(mode);
}

function applyThemeMode(mode) {
  let effective = mode;
  if (mode === 'system') {
    effective = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }

  document.documentElement.setAttribute('data-theme', effective);
  document.documentElement.setAttribute('data-theme-mode', mode);

  document.querySelectorAll('.theme-segment-btn').forEach(btn => {
    if (btn.dataset.themeBtn === mode) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });
}

// Init on Load
function initDashboard() {
  // Session Redirection Logic for user's session URL (e.g. /s/<session_id>)
  const urlParams = new URLSearchParams(window.location.search);
  const pathSegments = window.location.pathname.split('/').filter(Boolean);
  const reserved = ['static', 'api', 'dashboard.html', 'index.html', 'resumes', 'logs'];
  let currentSid = null;
  if (pathSegments.length === 2 && pathSegments[0] === 's') {
    currentSid = pathSegments[1];
  } else if (pathSegments.length === 1 && !reserved.includes(pathSegments[0]) && pathSegments[0] !== 's') {
    currentSid = pathSegments[0];
  } else {
    currentSid = urlParams.get('s');
  }

  if (currentSid && currentSid.startsWith('ap_')) {
    currentSid = currentSid.substring(3);
  }

  if (currentSid) {
    localStorage.setItem('applypilot_session_id', currentSid);
  } else {
    let savedSid = localStorage.getItem('applypilot_session_id');
    if (savedSid && savedSid.startsWith('ap_')) {
      savedSid = savedSid.substring(3);
      localStorage.setItem('applypilot_session_id', savedSid);
    }
  }

  initTheme();
  loadStateFromLocalStorage();
  restoreFilterButtonsUI();
  initSortDropdown();
  updateSearchTabBadges();

  const activeTabBtn = document.querySelector(`.search-tab-btn[data-profile-id="${selectedSearchProfile}"]`);
  if (activeTabBtn) {
    document.querySelectorAll('.search-tab-btn').forEach(b => b.classList.remove('active'));
    activeTabBtn.classList.add('active');
  }

  const activeStatusBtn = document.querySelector(`.status-nav .nav-item[data-status="${statusFilter}"]`);
  if (activeStatusBtn) {
    document.querySelectorAll('.status-nav .nav-item').forEach(b => b.classList.remove('active'));
    activeStatusBtn.classList.add('active');
  }

  updateRightPanelHeader(selectedSearchProfile);
  applyFilters();
  pollRunnerStatus();
  checkAiStatus();

  if (window.location.hash === '#open_drawer') {
    setTimeout(() => {
      const firstCard = document.querySelector('.job-card');
      if (firstCard) openJobDrawer(firstCard);
    }, 400);
  }

  if (window.location.hash === '#open_edit_modal') {
    setTimeout(() => {
      editSearchProfile('search_1', false);
      const modalDialog = document.querySelector('.modal-dialog');
      if (modalDialog) modalDialog.scrollTop = modalDialog.scrollHeight;
    }, 400);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDashboard);
} else {
  initDashboard();
}

function openSaveSearchesModal() {
  const modal = document.getElementById('save-searches-modal');
  const input = document.getElementById('modal-save-searches-url-input');
  
  const pathSegments = window.location.pathname.split('/').filter(Boolean);
  const reserved = ['static', 'api', 'dashboard.html', 'index.html', 'resumes', 'logs'];
  let currentSid = null;
  if (pathSegments.length === 2 && pathSegments[0] === 's') {
    currentSid = pathSegments[1];
  } else if (pathSegments.length === 1 && !reserved.includes(pathSegments[0]) && pathSegments[0] !== 's') {
    currentSid = pathSegments[0];
  } else {
    currentSid = new URLSearchParams(window.location.search).get('s');
  }

  if (!currentSid) {
    currentSid = localStorage.getItem('applypilot_session_id') || 'default';
  }

  const fullUrl = window.location.origin + '/s/' + currentSid;
  if (input) input.value = fullUrl;
  if (modal) modal.classList.add('active');
}

function closeSaveSearchesModal() {
  const modal = document.getElementById('save-searches-modal');
  if (modal) modal.classList.remove('active');
}

function copySaveSearchesUrl() {
  const input = document.getElementById('modal-save-searches-url-input');
  const fullUrl = input ? input.value : (window.location.origin + window.location.pathname + window.location.search);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(fullUrl).then(() => {
      showToast('Session URL copied to clipboard');
    }).catch(() => {
      if (input) {
        input.select();
        document.execCommand('copy');
        showToast('Session URL copied to clipboard');
      }
    });
  } else if (input) {
    input.select();
    document.execCommand('copy');
    showToast('Session URL copied to clipboard');
  }
}

// Global Exports
window.setThemeMode = setThemeMode;
window.applyThemeMode = applyThemeMode;
window.openJobDrawer = openJobDrawer;
window.closeCopilotDrawer = closeCopilotDrawer;
window.openSaveSearchesModal = openSaveSearchesModal;
window.closeSaveSearchesModal = closeSaveSearchesModal;
window.copySaveSearchesUrl = copySaveSearchesUrl;
window.handleDeleteAllUserData = handleDeleteAllUserData;

async function handleDeleteAllUserData() {
  if (!confirm("Are you sure you want to delete all your searches, CVs, saved jobs, and data?")) return;
  if (!confirm("FINAL CONFIRMATION: This action is permanent and cannot be undone. All your searches, uploaded CVs, and saved jobs will be permanently deleted. Do you want to proceed?")) return;

  try {
    const res = await fetch('/api/user/delete-all-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: getActiveSessionId() })
    });
    const data = await res.json();
    if (data.success) {
      document.cookie = 'ddw_sid=; Path=/; Max-Age=0; SameSite=Lax';
      try { localStorage.clear(); } catch(e) {}
      window.location.href = '/?new=1';
    } else {
      alert("Error deleting data: " + (data.error || "Unknown error"));
    }
  } catch (err) {
    document.cookie = 'ddw_sid=; Path=/; Max-Age=0; SameSite=Lax';
    try { localStorage.clear(); } catch(e) {}
    window.location.href = '/?new=1';
  }
}

function showToast(message) {
  let toast = document.getElementById('applypilot-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'applypilot-toast';
    toast.className = 'applypilot-toast';
    document.body.appendChild(toast);
  }
  toast.innerText = message;
  toast.classList.add('show');
  setTimeout(() => {
    toast.classList.remove('show');
  }, 3500);
}

function copyUniqueSessionUrl() {
  updateUniqueSessionUrlInput();
  const uniqueInput = document.getElementById('unique-session-url-input');
  const fullUrl = (uniqueInput && uniqueInput.value) ? uniqueInput.value : (window.location.origin + window.location.pathname + window.location.search);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(fullUrl).catch(() => {
      if (uniqueInput) {
        uniqueInput.select();
        document.execCommand('copy');
      }
    });
  } else if (uniqueInput) {
    uniqueInput.select();
    document.execCommand('copy');
  }
}

function initMobileSidebarState() {
  const isMobile = window.innerWidth <= 900;
  document.querySelectorAll('.sidebar-section').forEach(sec => {
    const icon = sec.querySelector('.section-toggle-icon');
    if (isMobile) {
      if (!sec.classList.contains('expanded')) {
        sec.classList.add('collapsed');
        if (icon) icon.innerHTML = '+';
      }
    } else {
      sec.classList.remove('collapsed');
      sec.classList.add('expanded');
      if (icon) icon.innerHTML = '&minus;';
    }
  });
}

// ── Application Copilot & Preparation Modal ─────────────────────────────────

function switchDrawerTab(tabName) {
  const tabDetails = document.getElementById('drawer-tab-details');
  const tabPrep = document.getElementById('drawer-tab-prep');
  const btnDetails = document.getElementById('tab-btn-details');
  const btnPrep = document.getElementById('tab-btn-prep');

  if (tabName === 'prep') {
    if (tabDetails) tabDetails.style.display = 'none';
    if (tabPrep) tabPrep.style.display = 'flex';
    if (btnDetails) {
      btnDetails.style.color = 'var(--text-secondary)';
      btnDetails.style.borderBottomColor = 'transparent';
      btnDetails.classList.remove('active');
    }
    if (btnPrep) {
      btnPrep.style.color = 'var(--primary)';
      btnPrep.style.borderBottomColor = 'var(--primary)';
      btnPrep.classList.add('active');
    }
    loadApplicationPrepData();
  } else {
    if (tabPrep) tabPrep.style.display = 'none';
    if (tabDetails) tabDetails.style.display = 'block';
    if (btnPrep) {
      btnPrep.style.color = 'var(--text-secondary)';
      btnPrep.style.borderBottomColor = 'transparent';
      btnPrep.classList.remove('active');
    }
    if (btnDetails) {
      btnDetails.style.color = 'var(--primary)';
      btnDetails.style.borderBottomColor = 'var(--primary)';
      btnDetails.classList.add('active');
    }
  }
}

function openJobPrep(card) {
  openJobDrawer(card);
  switchDrawerTab('prep');
}

function copyToClipboard(text, btnEl) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    if (btnEl) {
      const origText = btnEl.textContent;
      btnEl.textContent = 'Copied!';
      btnEl.style.background = '#dcfce7';
      btnEl.style.color = '#15803d';
      btnEl.style.borderColor = '#86efac';
      setTimeout(() => {
        btnEl.textContent = origText;
        btnEl.style.background = '';
        btnEl.style.color = '';
        btnEl.style.borderColor = '';
      }, 1800);
    }
  }).catch(err => {
    console.error('Clipboard copy error:', err);
  });
}

function copyTailoredText(type) {
  const elId = type === 'resume' ? 'prep-resume-text' : 'prep-cl-text';
  const ta = document.getElementById(elId);
  if (ta && ta.value) {
    copyToClipboard(ta.value, event.target);
  }
}

function renderFactCheckBadge(verification) {
  const badgeEl = document.getElementById('prep-fact-check-badge');
  if (!badgeEl) return;

  if (!verification) {
    badgeEl.style.display = 'none';
    return;
  }

  badgeEl.style.display = 'block';
  const isVerified = Boolean(verification.verified);
  const confidence = verification.confidence_level || (isVerified ? 'HIGH' : 'LOW');
  const score = verification.confidence_score !== undefined ? verification.confidence_score : (isVerified ? 100 : 50);

  if (isVerified) {
    badgeEl.style.background = '#f0fdf4';
    badgeEl.style.border = '1px solid #86efac';
    badgeEl.style.color = '#166534';
    let entitiesHtml = '';
    const ent = verification.verified_entities || {};
    const comps = (ent.companies || []).join(', ');
    const schools = (ent.schools || []).join(', ');
    if (comps || schools) {
      entitiesHtml = `<div style="margin-top:0.35rem; font-size:0.75rem; color:#15803d;">Verified employers: ${comps || 'Career history'} | Education: ${schools || 'Verified'}</div>`;
    }
    badgeEl.innerHTML = `
      <div style="font-weight:700; display:flex; align-items:center; gap:0.4rem;">
        <span>Fact-Check: 100% Verified (0 Hallucinations) &bull; Confidence Level ${confidence} (${score}%)</span>
      </div>
      <div style="font-size:0.75rem; margin-top:0.2rem;">This document strictly matches verified companies, titles, and achievements from your candidate profile.</div>
      ${entitiesHtml}
    `;
  } else {
    badgeEl.style.background = '#fef2f2';
    badgeEl.style.border = '1px solid #fecaca';
    badgeEl.style.color = '#991b1b';
    const errList = (verification.errors || verification.hallucinations_detected || []).slice(0, 3).map(e => `<li>${e}</li>`).join('');
    badgeEl.innerHTML = `
      <div style="font-weight:700;">Fact-Check Warning: Potential Discrepancies (${score}%)</div>
      <ul style="margin:0.25rem 0 0 1.25rem; font-size:0.75rem;">${errList || '<li>Detected skills or credentials that require review.</li>'}</ul>
    `;
  }
}

function getSessionIdFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get('s') || '';
}

function loadApplicationPrepData() {
  if (!activeDrawerCard) return;
  const url = activeDrawerCard.dataset.jobUrl;
  const sid = getSessionIdFromUrl();

  fetch('/api/application/prepare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: url, session_id: sid })
  })
  .then(res => res.json())
  .then(data => {
    if (!data.success) return;

    // 1. Populate Candidate Profile Grid
    const grid = document.getElementById('prep-fields-grid');
    if (grid && data.candidate) {
      const c = data.candidate;
      const fields = [
        { label: 'Full Name', value: c.full_name },
        { label: 'Email', value: c.email },
        { label: 'Phone', value: c.phone },
        { label: 'Location', value: c.location },
        { label: 'LinkedIn', value: c.linkedin_url },
        { label: 'GitHub / Portfolio', value: c.github_url || c.portfolio_url },
        { label: 'Experience', value: c.years_experience },
        { label: 'Work Authorization', value: c.work_authorization }
      ];

      grid.innerHTML = fields.filter(f => f.value).map(f => `
        <div style="background:var(--bg-card); border:1px solid var(--border-light); border-radius:8px; padding:0.6rem 0.75rem; display:flex; justify-content:space-between; align-items:center; gap:0.5rem;">
          <div style="min-width:0; flex:1;">
            <div style="font-size:0.7rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">${f.label}</div>
            <div style="font-size:0.8rem; font-weight:600; color:var(--text-primary); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(f.value)}">${escapeHtml(f.value)}</div>
          </div>
          <button class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.55rem; flex-shrink:0;" onclick="copyToClipboard('${escapeHtml(f.value).replace(/'/g, "\\'")}', this)">Copy</button>
        </div>
      `).join('');
    }

    // 2. Populate Common Questions & Answers
    const qaBox = document.getElementById('prep-qa-container');
    if (qaBox && data.common_answers) {
      const qa = data.common_answers;
      const questions = [
        { q: 'Why are you interested in this role / company?', a: qa.why_role },
        { q: 'What is your expected salary?', a: qa.salary_expectation },
        { q: 'Availability / Notice period', a: qa.notice_period }
      ];

      qaBox.innerHTML = questions.filter(item => item.a).map(item => `
        <div style="background:var(--bg-card); border:1px solid var(--border-light); border-radius:8px; padding:0.75rem; display:flex; flex-direction:column; gap:0.35rem;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:0.78rem; font-weight:700; color:var(--text-primary);">${item.q}</span>
            <button class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.55rem;" onclick="copyToClipboard('${escapeHtml(item.a).replace(/'/g, "\\'")}', this)">Copy Answer</button>
          </div>
          <div style="font-size:0.8rem; color:var(--text-secondary); line-height:1.45; background:var(--bg-app); border:1px solid var(--border-light); padding:0.5rem; border-radius:6px;">${escapeHtml(item.a)}</div>
        </div>
      `).join('');
    }

    // 3. Existing Tailored Assets
    const j = data.job || {};
    const resumeBox = document.getElementById('prep-resume-box');
    const resumeTextarea = document.getElementById('prep-resume-text');
    const resumePdfLink = document.getElementById('prep-resume-pdf-link');
    if (j.tailored_resume_text) {
      if (resumeBox) resumeBox.style.display = 'flex';
      if (resumeTextarea) resumeTextarea.value = j.tailored_resume_text;
      if (resumePdfLink && j.resume_pdf_url) {
        resumePdfLink.href = j.resume_pdf_url;
        resumePdfLink.style.display = 'inline-block';
      }
    } else {
      if (resumeBox) resumeBox.style.display = 'none';
    }

    const clBox = document.getElementById('prep-cl-box');
    const clTextarea = document.getElementById('prep-cl-text');
    const clPdfLink = document.getElementById('prep-cl-pdf-link');
    if (j.cover_letter_text) {
      if (clBox) clBox.style.display = 'flex';
      if (clTextarea) clTextarea.value = j.cover_letter_text;
      if (clPdfLink && j.cover_pdf_url) {
        clPdfLink.href = j.cover_pdf_url;
        clPdfLink.style.display = 'inline-block';
      }
    } else {
      if (clBox) clBox.style.display = 'none';
    }

    // 4. Verification Gate Result
    if (j.verification_report) {
      renderFactCheckBadge(j.verification_report);
    } else if (j.verification_status) {
      renderFactCheckBadge({
        verified: j.verification_status === 'verified',
        confidence_level: j.verification_confidence || 'HIGH',
        confidence_score: j.verification_status === 'verified' ? 100 : 70
      });
    } else {
      const badgeEl = document.getElementById('prep-fact-check-badge');
      if (badgeEl) badgeEl.style.display = 'none';
    }

    // 5. High-Match LinkedIn Outreach Strategy
    if (data.outreach) {
      renderOutreachStrategy(data.outreach, j);
    }
  })
  .catch(err => console.error('Error loading application prep data:', err));
}

function generateTailoredAsset(assetType) {
  if (!activeDrawerCard) return;
  const url = activeDrawerCard.dataset.jobUrl;
  const sid = getSessionIdFromUrl();

  const spinner = document.getElementById('prep-spinner');
  const btnResume = document.getElementById('btn-prep-generate-resume');
  const btnCl = document.getElementById('btn-prep-generate-cl');

  if (spinner) spinner.style.display = 'block';
  if (btnResume) btnResume.disabled = true;
  if (btnCl) btnCl.disabled = true;

  fetch('/api/application/tailor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: url, asset_type: assetType, session_id: sid })
  })
  .then(res => res.json())
  .then(data => {
    if (spinner) spinner.style.display = 'none';
    if (btnResume) btnResume.disabled = false;
    if (btnCl) btnCl.disabled = false;

    if (!data.success) {
      alert('Error generating document: ' + (data.error || 'Unknown error'));
      return;
    }

    // Render updated assets
    if (data.tailored_resume_text) {
      const resumeBox = document.getElementById('prep-resume-box');
      const resumeTextarea = document.getElementById('prep-resume-text');
      const resumePdfLink = document.getElementById('prep-resume-pdf-link');
      if (resumeBox) resumeBox.style.display = 'flex';
      if (resumeTextarea) resumeTextarea.value = data.tailored_resume_text;
      if (resumePdfLink && data.resume_pdf_url) {
        resumePdfLink.href = data.resume_pdf_url;
        resumePdfLink.style.display = 'inline-block';
      }
    }

    if (data.cover_letter_text) {
      const clBox = document.getElementById('prep-cl-box');
      const clTextarea = document.getElementById('prep-cl-text');
      const clPdfLink = document.getElementById('prep-cl-pdf-link');
      if (clBox) clBox.style.display = 'flex';
      if (clTextarea) clTextarea.value = data.cover_letter_text;
      if (clPdfLink && data.cover_pdf_url) {
        clPdfLink.href = data.cover_pdf_url;
        clPdfLink.style.display = 'inline-block';
      }
    }

    if (data.verification) {
      renderFactCheckBadge(data.verification);
    }
  })
  .catch(err => {
    if (spinner) spinner.style.display = 'none';
    if (btnResume) btnResume.disabled = false;
    if (btnCl) btnCl.disabled = false;
    console.error('Error generating tailored asset:', err);
    alert('Error generating asset: ' + err.message);
  });
}

function renderOutreachStrategy(outreach, job) {
  const sectionEl = document.getElementById('prep-outreach-section');
  const badgeHighFit = document.getElementById('prep-outreach-high-fit-badge');
  const archetypesGrid = document.getElementById('prep-outreach-archetypes');
  const messagesContainer = document.getElementById('prep-outreach-messages');

  if (!sectionEl || !outreach) return;

  const score = job && job.fit_score !== undefined ? Number(job.fit_score) : 0;
  const isHighFit = score >= 8 || score >= 80;

  if (badgeHighFit) {
    badgeHighFit.style.display = isHighFit ? 'inline-block' : 'none';
  }
  if (isHighFit) {
    sectionEl.style.borderColor = 'var(--primary)';
  } else {
    sectionEl.style.borderColor = 'var(--border-light)';
  }

  // Archetypes
  if (archetypesGrid && outreach.archetypes) {
    archetypesGrid.innerHTML = outreach.archetypes.map(arch => `
      <div style="background:var(--bg-card); border:1px solid var(--border-light); border-radius:8px; padding:0.6rem 0.75rem; display:flex; flex-direction:column; justify-content:space-between; gap:0.4rem;">
        <div>
          <div style="font-size:0.7rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;">${escapeHtml(arch.archetype_role)}</div>
          <div style="font-size:0.8rem; font-weight:600; color:var(--text-primary); margin-top:0.15rem;">${escapeHtml(arch.title_label)}</div>
        </div>
        <a href="${escapeHtml(arch.search_url)}" target="_blank" rel="noopener noreferrer" class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.55rem; text-align:center; text-decoration:none; display:inline-block;">Search on LinkedIn &rarr;</a>
      </div>
    `).join('');
  }

  // Messages
  if (messagesContainer && outreach.messages) {
    const msgs = outreach.messages;
    const items = [
      { key: 'connection_note', data: msgs.connection_note },
      { key: 'recruiter_pitch', data: msgs.recruiter_pitch },
      { key: 'peer_inquiry', data: msgs.peer_inquiry }
    ];

    messagesContainer.innerHTML = items.filter(it => it.data).map(it => {
      const d = it.data;
      const isConnection = it.key === 'connection_note';
      const countLabel = isConnection
        ? `${d.char_count || d.text.length} / ${d.max_chars || 300} chars`
        : `${d.word_count || d.text.split(/\s+/).length} words`;
      const isOverLimit = isConnection && (d.char_count || d.text.length) > 300;
      const countColor = isOverLimit ? '#dc2626' : 'var(--text-muted)';

      return `
        <div style="background:var(--bg-card); border:1px solid var(--border-light); border-radius:8px; padding:0.85rem; display:flex; flex-direction:column; gap:0.45rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.4rem;">
            <div style="display:flex; align-items:center; gap:0.5rem;">
              <span style="font-size:0.82rem; font-weight:700; color:var(--text-primary);">${escapeHtml(d.label)}</span>
              <span style="font-size:0.72rem; color:${countColor}; font-weight:600;">(${countLabel})</span>
            </div>
            <button class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.6rem;" onclick="copyToClipboard('${escapeHtml(d.text).replace(/'/g, "\\'")}', this)">Copy Message</button>
          </div>
          ${d.tip ? `<div style="font-size:0.72rem; color:var(--text-secondary);">${escapeHtml(d.tip)}</div>` : ''}
          <textarea class="form-input" rows="${isConnection ? 3 : 5}" readonly style="width:100%; font-size:0.78rem; font-family:inherit; background:var(--bg-app); border:1px solid var(--border-light); border-radius:6px; padding:0.5rem; line-height:1.45;">${escapeHtml(d.text)}</textarea>
        </div>
      `;
    }).join('');
  }
}

function regenerateOutreachLLM() {
  if (!activeDrawerCard) return;
  const url = activeDrawerCard.dataset.jobUrl;
  const sid = getSessionIdFromUrl();
  const btn = document.getElementById('btn-prep-generate-outreach');

  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Generating with AI...';
  }

  fetch('/api/outreach/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: url, session_id: sid, use_llm: true })
  })
  .then(res => res.json())
  .then(data => {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Refine with AI';
    }
    if (!data.success || !data.outreach) {
      alert('Error generating strategy: ' + (data.error || 'Unknown error'));
      return;
    }
    const scoreVal = activeDrawerCard.dataset.score ? Number(activeDrawerCard.dataset.score) : 0;
    renderOutreachStrategy(data.outreach, { fit_score: scoreVal });
  })
  .catch(err => {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Refine with AI';
    }
    console.error('Error generating outreach strategy:', err);
    alert('Server connection error: ' + err.message);
  });
}

// ── AI Engine Configuration Modal (BYOK) ──────────────────────────────────
let activeAiProvider = 'gemini';

function openAiConfigModal() {
  const modal = document.getElementById('ai-config-modal');
  if (!modal) return;
  modal.style.display = 'flex';
  const feedback = document.getElementById('ai-config-feedback');
  if (feedback) feedback.style.display = 'none';

  fetch('/api/llm/status')
    .then(res => res.json())
    .then(data => {
      if (data && data.provider) {
        switchAiProviderTab(data.provider);
        if (data.api_key_masked) {
          const input = document.getElementById(`ai-key-${data.provider}`);
          if (input && !input.value) {
            input.placeholder = `Actual: ${data.api_key_masked}`;
          }
        }
      }
    })
    .catch(err => console.error('Error fetching LLM status:', err));
}

function closeAiConfigModal() {
  const modal = document.getElementById('ai-config-modal');
  if (modal) modal.style.display = 'none';
}

function switchAiProviderTab(provider) {
  activeAiProvider = provider;
  const providers = ['gemini', 'groq', 'openai', 'ollama', 'custom'];

  document.querySelectorAll('.ai-provider-tab').forEach(btn => {
    const p = btn.dataset.provider;
    if (p === provider) {
      btn.classList.add('active');
      btn.style.background = 'var(--bg-app)';
      btn.style.color = 'var(--primary)';
      btn.style.fontWeight = '700';
    } else {
      btn.classList.remove('active');
      btn.style.background = 'none';
      btn.style.color = 'var(--text-secondary)';
      btn.style.fontWeight = '600';
    }
  });

  providers.forEach(p => {
    const panel = document.getElementById(`ai-tab-${p}`);
    if (panel) {
      panel.style.display = (p === provider) ? 'flex' : 'none';
    }
  });

  const feedback = document.getElementById('ai-config-feedback');
  if (feedback) feedback.style.display = 'none';
}

function getActiveAiPayload() {
  const payload = { provider: activeAiProvider };
  if (activeAiProvider === 'gemini') {
    const input = document.getElementById('ai-key-gemini');
    payload.api_key = input ? input.value.trim() : '';
    payload.model = 'gemini-2.0-flash';
  } else if (activeAiProvider === 'groq') {
    const input = document.getElementById('ai-key-groq');
    payload.api_key = input ? input.value.trim() : '';
    payload.model = 'llama-3.3-70b-versatile';
  } else if (activeAiProvider === 'openai') {
    const input = document.getElementById('ai-key-openai');
    payload.api_key = input ? input.value.trim() : '';
    payload.model = 'gpt-4o-mini';
  } else if (activeAiProvider === 'ollama') {
    const urlInput = document.getElementById('ai-url-ollama');
    const modelInput = document.getElementById('ai-model-ollama');
    payload.base_url = urlInput ? urlInput.value.trim() : 'http://localhost:11434/v1';
    payload.model = modelInput ? modelInput.value.trim() : 'llama3.2';
    payload.api_key = 'local-key';
  } else if (activeAiProvider === 'custom') {
    const urlInput = document.getElementById('ai-url-custom');
    const keyInput = document.getElementById('ai-key-custom');
    const modelInput = document.getElementById('ai-model-custom');
    payload.base_url = urlInput ? urlInput.value.trim() : '';
    payload.api_key = keyInput ? keyInput.value.trim() : '';
    payload.model = modelInput ? modelInput.value.trim() : '';
  }
  return payload;
}

function showAiFeedback(msg, isSuccess) {
  const fb = document.getElementById('ai-config-feedback');
  if (!fb) return;
  fb.style.display = 'block';
  if (isSuccess) {
    fb.style.background = '#f0fdf4';
    fb.style.border = '1px solid #86efac';
    fb.style.color = '#15803d';
    fb.innerHTML = `<strong>Connection successful:</strong> ${escapeHtml(msg)}`;
  } else {
    fb.style.background = '#fef2f2';
    fb.style.border = '1px solid #fecaca';
    fb.style.color = '#991b1b';
    fb.innerHTML = `<strong>Connection error:</strong> ${escapeHtml(msg)}`;
  }
}

async function testAiConfigConnection() {
  const btn = document.getElementById('btn-test-ai');
  const payload = getActiveAiPayload();

  if (activeAiProvider !== 'ollama' && !payload.api_key) {
    showAiFeedback('Please enter your API Key to test the connection.', false);
    return;
  }

  const origText = btn ? btn.textContent : 'Test Connection';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Testing connection...';
  }

  try {
    const res = await fetch('/api/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      const lat = data.latency_ms ? ` (${data.latency_ms}ms)` : '';
      showAiFeedback(`Connected to model ${data.model}${lat}. Ready to use.`, true);
    } else {
      showAiFeedback(data.message || 'Could not connect to provider.', false);
    }
  } catch (err) {
    showAiFeedback('Network error connecting to server: ' + err.message, false);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText;
    }
  }
}

async function saveAiConfig() {
  const btn = document.getElementById('btn-save-ai');
  const payload = getActiveAiPayload();

  if (activeAiProvider !== 'ollama' && !payload.api_key) {
    showAiFeedback('Please enter an API Key before saving.', false);
    return;
  }

  const origText = btn ? btn.textContent : 'Save & Activate';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Saving...';
  }

  try {
    const res = await fetch('/api/llm/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      showAiFeedback('Settings saved successfully.', true);
      checkAiStatus();
      setTimeout(() => {
        closeAiConfigModal();
      }, 700);
    } else {
      showAiFeedback(data.message || 'Error saving settings.', false);
    }
  } catch (err) {
    showAiFeedback('Error connecting to server: ' + err.message, false);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText;
    }
  }
}

async function checkAiStatus() {
  try {
    const res = await fetch('/api/llm/status');
    const data = await res.json();
    const dot = document.getElementById('ai-status-indicator');
    const label = document.getElementById('ai-status-label');
    if (!dot || !label) return;

    if (data.configured) {
      dot.style.background = '#10b981';
      const prov = (data.provider || 'AI').toUpperCase();
      const model = data.model ? ` (${data.model})` : '';
      label.textContent = `AI: ${prov}${model}`;
      label.style.color = 'var(--text-primary)';
    } else {
      dot.style.background = '#f59e0b';
      label.textContent = 'AI: Not configured';
      label.style.color = 'var(--text-secondary)';
    }
  } catch (err) {
    console.error('Error checking AI status:', err);
  }
}

window.openAiConfigModal = openAiConfigModal;
window.closeAiConfigModal = closeAiConfigModal;
window.switchAiProviderTab = switchAiProviderTab;
window.testAiConfigConnection = testAiConfigConnection;
window.saveAiConfig = saveAiConfig;
window.checkAiStatus = checkAiStatus;

window.switchDrawerTab = switchDrawerTab;
window.openJobPrep = openJobPrep;
window.copyToClipboard = copyToClipboard;
window.copyTailoredText = copyTailoredText;
window.generateTailoredAsset = generateTailoredAsset;
window.renderOutreachStrategy = renderOutreachStrategy;
window.regenerateOutreachLLM = regenerateOutreachLLM;

window.toggleSidebarSection = toggleSidebarSection;
window.toggleSubSection = toggleSubSection;
window.updateSearchTabsRunningState = updateSearchTabsRunningState;
window.updateRunnerBannerVisibility = updateRunnerBannerVisibility;
window.triggerRunAllSearches = triggerRunAllSearches;
window.setRunAllButtonSearching = setRunAllButtonSearching;
window.restoreRunAllButton = restoreRunAllButton;
window.updateSearchTabBadges = updateSearchTabBadges;

let currentProModalSource = 'sidebar';

function trackTelemetry(eventKey) {
  if (!eventKey) return;
  try {
    fetch('/api/telemetry/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event: eventKey })
    }).catch(() => {});
  } catch (e) {}
}

function openProWaitlistModal(source, reason, message) {
  currentProModalSource = source || 'quota_limit';
  const modal = document.getElementById('pro-waitlist-modal');
  if (!modal) return;

  const titleEl = document.getElementById('pro-modal-title');
  const desc = document.getElementById('pro-modal-quota-desc');
  const feedback = document.getElementById('pro-waitlist-feedback');
  if (feedback) feedback.style.display = 'none';

  if (titleEl) {
    titleEl.textContent = (reason === 'max_tabs') ? 'Free Tab Limit Reached' : 'Free Search Limit Reached';
  }
  if (desc) {
    desc.textContent = message || 'Sign up for the waitlist to unlock +1 free search now, or download the open-source repo to run locally with no limits.';
  }
  if (!reason && !message) {
    trackTelemetry('paywall_views');
  }

  modal.style.display = 'flex';
}

function closeProWaitlistModal() {
  const modal = document.getElementById('pro-waitlist-modal');
  if (modal) modal.style.display = 'none';
}

async function submitProWaitlist() {
  const emailInput = document.getElementById('pro-waitlist-email');
  const btn = document.getElementById('btn-submit-waitlist');
  const feedback = document.getElementById('pro-waitlist-feedback');

  const email = emailInput ? emailInput.value.trim() : '';
  const profilesList = window.allSearchProfiles || allSearchProfiles || [];
  const activeSp = profilesList.find(p => p.id === selectedSearchProfile) || profilesList[0];
  const targetRole = (activeSp && activeSp.name) ? safeDecodeText(activeSp.name) : '';

  if (!email || !email.includes('@')) {
    if (feedback) {
      feedback.style.display = 'block';
      feedback.style.background = 'rgba(239, 68, 68, 0.12)';
      feedback.style.color = '#dc2626';
      feedback.style.border = '1px solid rgba(239, 68, 68, 0.3)';
      feedback.textContent = 'Please enter a valid email address.';
    }
    if (emailInput) emailInput.focus();
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Joining Waitlist...';
  }

  try {
    const resp = await fetch('/api/waitlist/join', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: email,
        target_role: targetRole,
        source: currentProModalSource || 'pro_modal'
      })
    });
    const data = await resp.json();
    if (data.success) {
      if (feedback) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(16, 185, 129, 0.12)';
        feedback.style.color = '#059669';
        feedback.style.border = '1px solid rgba(16, 185, 129, 0.3)';
        feedback.textContent = data.message || 'Waitlist joined! +1 free search unlocked.';
      }
      if (btn) {
        btn.textContent = 'Waitlist Joined (+1 Search Unlocked)';
      }
      if (typeof showToast === 'function') {
        showToast('Waitlist joined! +1 free search unlocked.');
      }
    } else {
      if (feedback) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(239, 68, 68, 0.12)';
        feedback.style.color = '#dc2626';
        feedback.style.border = '1px solid rgba(239, 68, 68, 0.3)';
        feedback.textContent = data.error || 'Could not join waitlist.';
      }
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Join Waitlist (+1 Free Search)';
      }
    }
  } catch (err) {
    if (feedback) {
      feedback.style.display = 'block';
      feedback.style.background = 'rgba(239, 68, 68, 0.12)';
      feedback.style.color = '#dc2626';
      feedback.style.border = '1px solid rgba(239, 68, 68, 0.3)';
      feedback.textContent = 'Network error submitting waitlist request.';
    }
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Join Waitlist (+1 Free Search)';
    }
  }
}

window.openProWaitlistModal = openProWaitlistModal;
window.closeProWaitlistModal = closeProWaitlistModal;
window.submitProWaitlist = submitProWaitlist;
window.trackTelemetry = trackTelemetry;

let currentAutoRunInterval = '1h';

function updateAutoRunUI(enabled, interval) {
  currentAutoRunInterval = interval || currentAutoRunInterval || '1h';
  const isEnabled = Boolean(enabled);

  const stb = document.getElementById('sidebar-auto-run-status-text');
  if (stb) {
    stb.textContent = isEnabled ? 'On' : 'Off';
    stb.style.color = isEnabled ? '#10b981' : 'var(--text-muted, #64748b)';
  }

  const sw = document.getElementById('sidebar-auto-search-wrapper');
  if (sw) {
    if (isEnabled) {
      sw.style.borderColor = 'rgba(16, 185, 129, 0.45)';
      sw.style.background = 'rgba(16, 185, 129, 0.1)';
    } else {
      sw.style.borderColor = 'var(--border-light, rgba(255, 255, 255, 0.15))';
      sw.style.background = 'var(--bg-card, #151d30)';
    }
  }

  const st = document.getElementById('auto-run-toggle-sidebar');
  if (st) st.checked = isEnabled;

  const sbg = document.getElementById('auto-run-interval-badge-sidebar');
  if (sbg) sbg.textContent = currentAutoRunInterval;
}

function toggleAutoRunFromSidebar() {
  const st = document.getElementById('auto-run-toggle-sidebar');
  const newState = st ? !st.checked : true;
  toggleAutoRun(newState);
}

async function initAutoRunState() {
  try {
    const res = await fetch('/api/settings/auto-run?_t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    updateAutoRunUI(data.enabled, data.interval);
  } catch (e) {
    console.debug('Failed to fetch auto-run status:', e);
  }
}

async function toggleAutoRun(enabled) {
  const isEnabled = Boolean(enabled);
  updateAutoRunUI(isEnabled, currentAutoRunInterval);

  try {
    const res = await fetch('/api/settings/auto-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: isEnabled }),
    });
    if (res.ok) {
      const data = await res.json();
      const settings = data.settings || {};
      const intv = settings.interval || currentAutoRunInterval;
      updateAutoRunUI(settings.enabled, intv);

      const intvLabelMap = {
        '15m': 'every 15 mins',
        '30m': 'every 30 mins',
        '1h': 'every 1 hour',
        '2h': 'every 2 hours',
      };
      const label = intvLabelMap[intv] || ('every ' + intv);

      if (isEnabled) {
        showToast('Auto Search enabled (' + label + ')');
      } else {
        showToast('Auto Search disabled');
      }
    } else {
      updateAutoRunUI(!isEnabled, currentAutoRunInterval);
      showToast('Failed to update Auto Search setting');
    }
  } catch (e) {
    updateAutoRunUI(!isEnabled, currentAutoRunInterval);
    showToast('Network error updating Auto Search');
  }
}

window.initAutoRunState = initAutoRunState;
window.toggleAutoRun = toggleAutoRun;
window.toggleAutoRunFromSidebar = toggleAutoRunFromSidebar;

document.addEventListener('DOMContentLoaded', () => {
  initMobileSidebarState();
  initAutoRunState();
  checkAiStatus();
  updateSearchTabBadges();
  checkForSystemUpdates();
  const cardCount = document.querySelectorAll('.job-card').length;
  const isNewlyCreated = localStorage.getItem('applypilot_running_search_id');
  if (isNewlyCreated) {
    runningSearchProfileId = isNewlyCreated;
  }
  updateSearchTabsRunningState(runningSearchProfileId, isRunnerActive || Boolean(isNewlyCreated));

  if (isRunnerActive || isNewlyCreated) {
    const skGrid = document.getElementById('skeleton-grid-container');
    const jobGrid = document.getElementById('job-grid-container');
    const emptyState = document.getElementById('no-jobs-empty-state');
    if (skGrid) skGrid.style.display = 'flex';
    if (jobGrid) jobGrid.style.display = 'none';
    if (emptyState) emptyState.style.display = 'none';

    const banner = document.getElementById('runner-status-banner');
    const statusTxt = document.getElementById('runner-status-text');
    if (banner) banner.style.display = 'flex';
    if (statusTxt) statusTxt.textContent = "Searching job listings across enabled sources...";

    pollRunnerStatus();
    startPollingRunnerStatus();
  } else if (cardCount === 0) {
    const skGrid = document.getElementById('skeleton-grid-container');
    const jobGrid = document.getElementById('job-grid-container');
    const emptyState = document.getElementById('no-jobs-empty-state');
    if (skGrid) skGrid.style.display = 'none';
    if (jobGrid) jobGrid.style.display = 'none';
    if (emptyState) {
      emptyState.style.display = 'block';
    }
  }
});
initMobileSidebarState();

// Auto-refresh search updated relative timestamp live every 10 seconds
setInterval(() => {
  const updatedTextEl = document.getElementById('search-updated-timestamp-text');
  if (updatedTextEl && selectedSearchProfile) {
    updateRightPanelHeader(selectedSearchProfile);
  }
}, 10000);

// Immediately update timestamp and poll when returning to tab or focusing window
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    if (selectedSearchProfile) {
      updateRightPanelHeader(selectedSearchProfile);
    }
    if (typeof pollRunnerStatus === 'function') {
      pollRunnerStatus();
    }
  }
});

window.addEventListener('focus', () => {
  if (selectedSearchProfile) {
    updateRightPanelHeader(selectedSearchProfile);
  }
  if (typeof pollRunnerStatus === 'function') {
    pollRunnerStatus();
  }
});




