// UI localisation. Ukrainian is the default; the choice is stored in the `lang` cookie.
// Category / sub-category names come from data/index.json (`label` / `label_uk`).

export const LANGS = ['uk', 'en'];
const COOKIE = 'lang';

const STRINGS = {
  uk: {
    'app.title': 'Структура міст',
    'app.description': 'Яку частку території міста займають будівлі, дороги, залізниці, парки, вода та інші об’єкти — за даними OpenStreetMap.',
    'tab.map': 'Мапа',
    'tab.compare': 'Порівняння міст',
    'tab.about': 'Методика та дані',
    'city': 'Місто',
    'city2': 'Порівняти з',
    'city2.none': '— Без міста —',
    'dual.scaleNote': 'Обидві мапи мають однаковий масштаб і наближаються разом; переміщувати кожну можна окремо.',
    'lang.switch': 'Мова інтерфейсу',
    'map.basemap': 'Базова мапа',
    'map.boundary': 'Межа',
    'map.opacity': 'Прозорість',
    'map.zoomHint': 'Наблизьте мапу, щоб переглянути окремі об’єкти',
    'unit.pctArea': '% площі',
    'unit.km2': 'км²',
    'unit.pctCity': '% площі міста',
    'unit.km2abs': 'км² (абсолютно)',
    'unit.pctOf': '% від: {what}',
    'units.km2': 'км²',
    'units.m2': 'м²',
    'city.meta': 'Адміністративна площа {area} км² · відношення OSM ',
    'city.osmDate': 'Дані OSM станом на {date}',
    'legend.show': 'Показати',
    'legend.hide': 'Сховати',
    'legend.subToggle': 'Перемкнути (діє при наближенні)',
    'legend.subPctOfCat': 'Підкатегорії — у % від категорії',
    'legend.note': 'Категорії взаємовиключні й разом становлять 100% адміністративної площі. Підкатегорії перемикаються при наближенні.',
    'tip.ofCity': 'від площі міста',
    'tip.ofCat': 'від категорії «{cat}»',
    'tip.features': 'об’єктів: {n}',
    'b.title': 'Будівлі',
    'b.count': 'Кількість будівель',
    'b.footprint': 'Загальна площа забудови',
    'b.mean': 'Середня площа будівлі',
    'b.unknown': 'Невідомий тип (building=yes)',
    'b.unknownVal': '{n} · {pct}% площі',
    'b.secondary': 'Тип за додатковими тегами',
    'b.levels': 'З тегом building:levels',
    'q.title': 'Якість даних',
    'status.ok': 'ОК',
    'status.warn': 'Увага',
    'status.fail': 'Помилка',
    'popup.area': 'Ексклюзивна площа',
    'popup.tag': 'Тег OSM',
    'popup.width': 'Прийнята ширина',
    'popup.widthVal': '{w} м (буфер лінії)',
    'popup.osm': 'Відкрити {id} в OpenStreetMap ↗',
    'popup.noData': 'Ділянка не покрита жодним класифікованим об’єктом OSM.',
    'popup.note': 'Показано частину, не перекриту об’єктами вищого пріоритету, обрізану межею міста.',
    'cmp.title': 'Структура території міст',
    'cmp.breakdown': 'Розбивка',
    'cmp.all': 'Усі категорії',
    'cmp.byType': '{cat} за типами',
    'cmp.hint.km2': 'Довжина смуги пропорційна абсолютній площі — розміри міст можна порівнювати напряму.',
    'cmp.hint.pctcat': 'Кожна смуга — це 100% обраної категорії в межах міста.',
    'cmp.hint.pct': 'Кожна смуга — це 100% адміністративної площі.',
    'cmp.hint.pctSub': 'Сегменти показують частку від усієї адміністративної площі; решта смуги — інші землі.',
    'cmp.category': 'Категорія',
    'cmp.total': 'Разом (адміністративна площа)',
    'cmp.ofCityArea': 'від площі міста',
    'cmp.ofCategory': 'від категорії',
    'about.error': 'Не вдалося завантажити опис методики.',
    'about.city': 'Місто',
    'about.area': 'Площа',
    'about.relation': 'Межа в OSM',
    'about.osmDate': 'Дані OSM',
    'cmp.table': 'Таблиця даних',
    'terr.label': 'Територія',
    'terr.city': 'Місто',
    'terr.hromada': 'Громада',
    'terr.greater': 'Місто з передмістями',
    'terr.city.hint': 'Лише адміністративні межі міста',
    'terr.hromada.hint': 'Уся міська територіальна громада',
    'terr.greater.hint': 'Місто разом із населеними пунктами в межах 10 хв їзди автомобілем',
    'terr.note.same_as_city': 'Громада збігається з межами міста.',
    'terr.note.no_hromada': 'Місто не входить до окремої громади — показано межі міста.',
    'terr.note.occupied': 'Тимчасово окуповане місто — показано межі міста.',
    'terr.note.not_computed': 'Режим «{mode}» для цього міста ще не розраховано — показано межі міста.',
    'city.metaTerr': 'Площа території ({mode}) {area} км² · межа міста в OSM ',
    'set.title': '{city}Населені пункти «Міста з передмістями»',
    'set.rule': 'Типовий час у дорозі автомобілем до забудови міста — не більше {d} хв (без заторів).',
    'set.min': 'хв',
    'set.approx': 'контур приблизний: в OSM населений пункт позначено лише точкою',
    'cmp.landNote': 'Зверніть увагу: «{res}» та «{ind}» — це лише земля цих територій без розташованих на ній будівель (вони враховані в категорії «{bld}»), а також без доріг, парковок, зелених зон тощо, які мають власні категорії.',
    'cmp.focus': 'Фокус: {cat}',
    'cmp.focusClear': 'Скинути фокус',
    'cmp.focusHint': 'Клацніть категорію на смузі чи в легенді, щоб порівняти її між містами.',
    'cmp.hint.focus': 'Показано лише «{cat}»: міста впорядковано за спаданням, найбільше значення займає всю ширину. Клацніть смугу ще раз або ✕, щоб повернутися до повної структури.',
    'cmp.openMap': 'Відкрити {city} на мапі',
    'cmp.noCities': 'Виберіть хоча б одне місто у фільтрі «Міста».',
    'cf.button': 'Міста: {n} з {total}',
    'cf.search': 'Пошук міста…',
    'cf.all': 'Усі',
    'cf.none': 'Жодного',
    'cf.noMatch': 'Нічого не знайдено',
    'cf.groupAll': 'Обрати всю групу',
    'cf.groupNone': 'Зняти вибір з групи',
    'author': 'Про автора',
    'nodata': 'Даних не знайдено. Спершу виконайте {cmd}.',
  },
  en: {
    'app.title': 'Urban Footprint',
    'app.description': "How much of each city's territory is occupied by buildings, roads, railways, parks, water and other features — computed from OpenStreetMap.",
    'tab.map': 'Map',
    'tab.compare': 'Compare cities',
    'tab.about': 'Method & data',
    'city': 'City',
    'city2': 'Compare with',
    'city2.none': '— No city —',
    'dual.scaleNote': 'Both maps use the same scale and zoom together; each can be panned on its own.',
    'lang.switch': 'Interface language',
    'map.basemap': 'Base map',
    'map.boundary': 'Boundary',
    'map.opacity': 'Opacity',
    'map.zoomHint': 'Zoom in to inspect individual features',
    'unit.pctArea': '% of area',
    'unit.km2': 'km²',
    'unit.pctCity': '% of city area',
    'unit.km2abs': 'km² (absolute)',
    'unit.pctOf': '% of {what}',
    'units.km2': 'km²',
    'units.m2': 'm²',
    'city.meta': 'Administrative area {area} km² · OSM relation ',
    'city.osmDate': 'OSM data as of {date}',
    'legend.show': 'Show',
    'legend.hide': 'Hide',
    'legend.subToggle': 'Toggle (applies to zoomed-in vector view)',
    'legend.subPctOfCat': 'Sub-categories as % of category',
    'legend.note': 'Categories are mutually exclusive and sum to 100% of the administrative area. Sub-category toggles apply when zoomed in.',
    'tip.ofCity': 'of city',
    'tip.ofCat': 'of {cat}',
    'tip.features': '{n} features',
    'b.title': 'Buildings',
    'b.count': 'Buildings',
    'b.footprint': 'Total footprint',
    'b.mean': 'Mean footprint',
    'b.unknown': 'Unknown type (building=yes)',
    'b.unknownVal': '{n} · {pct}% of area',
    'b.secondary': 'Typed via secondary tags',
    'b.levels': 'With building:levels',
    'q.title': 'Data quality',
    'status.ok': 'OK',
    'status.warn': 'Warn',
    'status.fail': 'Fail',
    'popup.area': 'Exclusive area',
    'popup.tag': 'OSM tag',
    'popup.width': 'Assumed width',
    'popup.widthVal': '{w} m (line buffered)',
    'popup.osm': 'View {id} on OpenStreetMap ↗',
    'popup.noData': 'Area not covered by any classified OSM feature.',
    'popup.note': 'Area shown is the part not covered by higher-priority features, clipped to the city boundary.',
    'cmp.title': 'City composition',
    'cmp.breakdown': 'Breakdown',
    'cmp.all': 'All categories',
    'cmp.byType': '{cat} by type',
    'cmp.hint.km2': 'Bar length proportional to absolute area — cities are directly comparable in size.',
    'cmp.hint.pctcat': 'Each bar is 100% of the selected category within the city.',
    'cmp.hint.pct': 'Each bar is 100% of the administrative area.',
    'cmp.hint.pctSub': 'Segments show the share of the whole administrative area; the rest of the bar is other land.',
    'cmp.category': 'Category',
    'cmp.total': 'Total (administrative area)',
    'cmp.ofCityArea': 'of city area',
    'cmp.ofCategory': 'of category',
    'about.error': 'Could not load methodology.',
    'about.city': 'City',
    'about.area': 'Area',
    'about.relation': 'OSM boundary',
    'about.osmDate': 'OSM data',
    'cmp.table': 'Data table',
    'terr.label': 'Territory',
    'terr.city': 'City',
    'terr.hromada': 'Hromada',
    'terr.greater': 'Greater City',
    'terr.city.hint': 'Administrative city boundary only',
    'terr.hromada.hint': 'The whole urban territorial community (hromada)',
    'terr.greater.hint': 'The city plus settlements within a 10-minute drive',
    'terr.note.same_as_city': 'The hromada coincides with the city boundary.',
    'terr.note.no_hromada': 'The city is not part of a separate hromada — city boundary shown.',
    'terr.note.occupied': 'Temporarily occupied city — city boundary shown.',
    'terr.note.not_computed': '“{mode}” has not been computed for this city yet — city boundary shown.',
    'city.metaTerr': 'Territory area ({mode}) {area} km² · OSM city boundary ',
    'set.title': '{city}Greater City settlements',
    'set.rule': 'Typical drive time to the city’s built-up area: at most {d} min (no traffic).',
    'set.min': 'min',
    'set.approx': 'approximate outline: OSM maps this settlement only as a point',
    'cmp.landNote': 'Note: “{res}” and “{ind}” cover only the land of these areas, without the buildings standing on it (those are counted under “{bld}”), and without roads, parking, green space etc., which have their own categories.',
    'cmp.focus': 'Focus: {cat}',
    'cmp.focusClear': 'Clear focus',
    'cmp.focusHint': 'Click a category on a bar or in the legend to compare it across cities.',
    'cmp.hint.focus': 'Showing only “{cat}”: cities sorted high to low, the largest value spans the full width. Click a bar again or ✕ to return to the full composition.',
    'cmp.openMap': 'Open {city} on the map',
    'cmp.noCities': 'Select at least one city in the “Cities” filter.',
    'cf.button': 'Cities: {n} of {total}',
    'cf.search': 'Search city…',
    'cf.all': 'All',
    'cf.none': 'None',
    'cf.noMatch': 'No matches',
    'cf.groupAll': 'Select the whole group',
    'cf.groupNone': 'Clear the group',
    'author': 'About the author',
    'nodata': 'No data found. Run {cmd} first.',
  },
};

// Validation check messages, rebuilt from the check id + value so they can be translated.
// Unknown ids fall back to the (English) message produced by the pipeline.
const CHECKS = {
  uk: {
    area_closure: (v, f) => `Сума категорій збігається з площею межі (розбіжність ${f(v, 5)}%)`,
    pieces_closure: (v, f) => `Сума експортованих фрагментів збігається з площею тайлів (розбіжність ${f(v, 5)}%)`,
    no_overlap_sample: (v, f) => `Перекриття фрагментів у вибірці: ${f(v, 6)}% їхньої площі (округлення до сітки 1 см)`,
    unclassified_share: (v, f) => `Частка території без класифікованих даних OSM: ${f(v, 2)}%`,
    unknown_buildings: (v, f) => `Будівлі невідомого типу: ${f(v, 1)}% площі забудови`,
    secondary_tag_buildings: (v, f) => `Будівель, тип яких визначено за додатковими тегами того самого об’єкта: ${f(v, 0)}`,
    roads_default_widths: (v, f) => `Дороги: ${f(v, 1)}% площі, оціненої за осьовими лініями, розраховано за типовою шириною`,
    railways_default_widths: (v, f) => `Залізниці: ${f(v, 1)}% площі, оціненої за осьовими лініями, розраховано за типовою шириною`,
    unmatched_features: (v, f) => `Об’єктів OSM, що не підпали під жодне правило: ${f(v, 0)}`,
    invalid_geometries: (v, f) => `Виправлено некоректних полігонів: ${f(v, 0)}`,
    geometry_failures: (v, f) => `Геометрій, які не вдалося побудувати (у всьому вихідному файлі): ${f(v, 0)}`,
    suspicious_features: (v, f) => `Підозріло великих полігонів доріг / залізниць / парковок (можливі помилки тегів OSM): ${f(v, 0)}`,
  },
};

function readCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : null;
}

let current = (() => {
  const c = readCookie(COOKIE);
  return LANGS.includes(c) ? c : 'uk';
})();

export const getLang = () => current;
export const locale = () => (current === 'uk' ? 'uk-UA' : 'en-US');

export function setLang(lang) {
  if (!LANGS.includes(lang)) return;
  current = lang;
  document.cookie = `${COOKIE}=${lang}; max-age=${60 * 60 * 24 * 365}; path=/; SameSite=Lax`;
  applyStatic();
}

export function t(key, vars = {}) {
  const s = STRINGS[current][key] ?? STRINGS.en[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : `{${k}}`));
}

/** Localised label of a category / sub-category definition from index.json. */
export const label = (def) => (def ? (current === 'uk' && def.label_uk) || def.label : '');

export function checkMessage(check, fmtNum) {
  const tpl = CHECKS[current]?.[check.id];
  return tpl && typeof check.value === 'number' ? tpl(check.value, fmtNum) : check.message;
}

/** Fill static markup: elements with data-i18n (text), data-i18n-title, data-i18n-aria. */
export function applyStatic() {
  document.documentElement.lang = current;
  document.title = t('app.title');
  document.querySelector('meta[name=description]')?.setAttribute('content', t('app.description'));
  document.querySelectorAll('[data-i18n]').forEach((e) => (e.textContent = t(e.dataset.i18n)));
  document.querySelectorAll('[data-i18n-title]').forEach((e) => e.setAttribute('title', t(e.dataset.i18nTitle)));
  document.querySelectorAll('[data-i18n-aria]').forEach((e) => e.setAttribute('aria-label', t(e.dataset.i18nAria)));
  document.querySelectorAll('[data-i18n-placeholder]').forEach((e) => e.setAttribute('placeholder', t(e.dataset.i18nPlaceholder)));
  document.querySelectorAll('.lang-switch button').forEach((b) => {
    b.classList.toggle('active', b.dataset.lang === current);
    b.setAttribute('aria-pressed', b.dataset.lang === current);
  });
}

// MapLibre control tooltips
export const MAP_LOCALE = {
  uk: {
    'NavigationControl.ZoomIn': 'Наблизити',
    'NavigationControl.ZoomOut': 'Віддалити',
    'NavigationControl.ResetBearing': 'Скинути поворот на північ',
    'AttributionControl.ToggleAttribution': 'Показати / сховати атрибуцію',
    'ScaleControl.Meters': 'м',
    'ScaleControl.Kilometers': 'км',
  },
  en: {
    'NavigationControl.ZoomIn': 'Zoom in',
    'NavigationControl.ZoomOut': 'Zoom out',
    'NavigationControl.ResetBearing': 'Reset bearing to north',
    'AttributionControl.ToggleAttribution': 'Toggle attribution',
    'ScaleControl.Meters': 'm',
    'ScaleControl.Kilometers': 'km',
  },
};
