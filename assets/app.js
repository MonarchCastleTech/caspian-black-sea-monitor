const COMPONENTS = {
  maritime_flow: ['Maritime flow', '30%', 'Regional AIS distortion'],
  nato_posture: ['NATO posture', '25%', 'Official-language shift'],
  regional_sanctions: ['Regional sanctions', '20%', 'OFAC action velocity'],
  commodity_dislocation: ['Commodity signal', '15%', 'Brent and wheat dislocation'],
  port_weather: ['Port weather', '10%', 'Seven-day forecast'],
};

const ui = {
  data: null,
  async init() {
    try {
      const response = await fetch('data/output.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.data = await response.json();
      this.render();
    } catch (error) { this.error(error); }
  },
  render() {
    const { meta, warning, components } = this.data;
    this.text('mode', meta.mode === 'live' ? 'Live · 6h refresh' : 'Retained snapshot');
    this.text('generated', `Updated ${this.date(meta.generated)}`);
    this.text('score', Number(warning.score).toFixed(1));
    this.text('status', warning.status);
    this.text('headline', warning.headline);
    this.text('interpretation', warning.interpretation);
    this.text('confidence', meta.confidence);
    this.text('coverage', meta.coverage);
    this.text('bonus', warning.concurrence_bonus ? `+${warning.concurrence_bonus.toFixed(0)}` : '+0');
    document.body.dataset.status = warning.status.toLowerCase();
    document.getElementById('score-ring').style.setProperty('--score', `${warning.score * 3.6}deg`);
    const notes = meta.source_notes || [];
    this.text('status-strip', notes.length ? `${meta.coverage} components available. ${notes.join(' ')}` : `${meta.coverage} components live · ${meta.confidence.toLowerCase()} confidence · next automated refresh within 6 hours.`);
    this.components(components);
    this.flow(components.maritime_flow);
    this.institutional('nato-list', 'nato-meta', components.nato_posture, 'posture_terms');
    this.institutional('ofac-list', 'ofac-meta', components.regional_sanctions, 'posture_terms');
    this.markets(components.commodity_dislocation);
    this.weather(components.port_weather);
    this.history(this.data.history || []);
    this.sources(this.data.sources || []);
  },
  components(components) {
    const root = document.getElementById('components');
    root.replaceChildren(...Object.entries(COMPONENTS).map(([key, labels]) => {
      const item = components[key] || {};
      const card = document.createElement('article'); card.className = 'component-card';
      const top = document.createElement('div'); top.className = 'component-top';
      const name = document.createElement('span'); name.textContent = labels[0];
      const weight = document.createElement('b'); weight.textContent = labels[1]; top.append(name, weight);
      const value = document.createElement('strong'); value.textContent = item.available ? Number(item.score).toFixed(1) : '—';
      const meter = document.createElement('div'); meter.className = 'meter'; const fill = document.createElement('i'); fill.style.width = `${item.score || 0}%`; meter.append(fill);
      const note = document.createElement('small'); note.textContent = `${labels[2]} · ${item.status || 'unavailable'}${item.retained ? ' · retained' : ''}`;
      card.append(top, value, meter, note); return card;
    }));
  },
  flow(component) {
    const body = document.getElementById('flow-rows'); const rows = (component && component.evidence) || [];
    if (!rows.length) return this.emptyRow(body, 5, 'No validated flow series.');
    body.replaceChildren(...rows.slice(0, 14).map(item => {
      const row = document.createElement('tr');
      const name = document.createElement('td'); name.innerHTML = '<b></b><small></small>'; name.querySelector('b').textContent = item.name; name.querySelector('small').textContent = item.country || '—';
      const basin = document.createElement('td'); basin.textContent = item.basin || '—';
      const direction = document.createElement('td'); const badge = document.createElement('span'); badge.className = `direction ${item.direction}`; badge.textContent = item.direction; direction.append(badge);
      const change = document.createElement('td'); change.className = item.change_pct >= 0 ? 'positive' : 'negative'; change.textContent = `${item.change_pct >= 0 ? '+' : ''}${Number(item.change_pct).toFixed(1)}%`;
      const pressure = document.createElement('td'); pressure.innerHTML = '<div class="inline-score"><span></span><i><em></em></i></div>'; pressure.querySelector('span').textContent = Number(item.pressure).toFixed(1); pressure.querySelector('em').style.width = `${item.pressure}%`;
      row.append(name, basin, direction, change, pressure); return row;
    }));
  },
  institutional(listId, metaId, component, termKey) {
    const root = document.getElementById(listId); const rows = (component && component.evidence) || [];
    this.text(metaId, `${Number(component?.score || 0).toFixed(1)} · z ${Number(component?.anomaly_z || 0).toFixed(2)}`);
    if (!rows.length) return this.emptyBlock(root, 'No matching publication inside the evidence window.');
    root.replaceChildren(...rows.slice(0, 6).map(item => {
      const link = document.createElement(item.url ? 'a' : 'div'); link.className = 'signal';
      if (item.url) { link.href = item.url.startsWith('/') ? `https://ofac.treasury.gov${item.url}` : item.url; link.target = '_blank'; link.rel = 'noopener'; }
      const meta = document.createElement('div'); const date = document.createElement('span'); date.textContent = item.date; const terms = document.createElement('em'); terms.textContent = (item[termKey] || []).slice(0, 3).join(' · ') || 'regional mention'; meta.append(date, terms);
      const title = document.createElement('b'); title.textContent = item.title; link.append(meta, title); return link;
    }));
  },
  markets(component) {
    const root = document.getElementById('market-grid'); const rows = (component && component.evidence) || [];
    if (!rows.length) return this.emptyBlock(root, 'Market series unavailable.');
    root.replaceChildren(...rows.map(item => {
      const link = document.createElement('a'); link.className = 'market-card'; link.href = item.source_url; link.target = '_blank'; link.rel = 'noopener';
      const title = document.createElement('div'); title.innerHTML = '<b></b><small></small>'; title.querySelector('b').textContent = item.label; title.querySelector('small').textContent = item.latest_date;
      const value = document.createElement('strong'); value.textContent = Number(item.latest_value).toFixed(2);
      const change = document.createElement('span'); change.className = item.direction; change.textContent = `${item.change_pct >= 0 ? '+' : ''}${Number(item.change_pct).toFixed(2)}% · z ${Number(item.robust_z).toFixed(2)}`;
      const pressure = document.createElement('em'); pressure.textContent = `${Number(item.score).toFixed(1)} pressure`; link.append(title, value, change, pressure); return link;
    }));
  },
  weather(component) {
    const root = document.getElementById('weather-grid'); const rows = (component && component.evidence) || [];
    if (!rows.length) return this.emptyBlock(root, 'Forecast unavailable.');
    root.replaceChildren(...rows.slice(0, 8).map(item => {
      const card = document.createElement('article'); card.className = 'weather-card';
      const title = document.createElement('div'); title.innerHTML = '<b></b><small></small>'; title.querySelector('b').textContent = item.name; title.querySelector('small').textContent = item.basin;
      const values = document.createElement('div'); values.className = 'weather-values'; values.innerHTML = '<span><b></b> m/s<small>wind</small></span><span><b></b> mm<small>rain</small></span>'; values.children[0].querySelector('b').textContent = Number(item.max_wind_ms).toFixed(1); values.children[1].querySelector('b').textContent = Number(item.max_precip_24h_mm).toFixed(1);
      const risk = document.createElement('em'); risk.textContent = `${Number(item.pressure).toFixed(1)} pressure`; card.append(title, values, risk); return card;
    }));
  },
  history(items) {
    const root = document.getElementById('history-chart'); if (!items.length) return this.emptyBlock(root, 'History begins with this run.');
    const width = 1000, height = 240, pad = 32; const values = items.map(item => Number(item.score)).filter(Number.isFinite);
    const points = values.map((value, index) => [values.length === 1 ? width / 2 : pad + index * (width - pad * 2) / (values.length - 1), height - pad - value / 100 * (height - pad * 2), value]);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    [25,45,65,80].forEach(level => { const y = height - pad - level / 100 * (height - pad * 2); const line = document.createElementNS(svg.namespaceURI,'line'); line.setAttribute('x1',pad); line.setAttribute('x2',width-pad); line.setAttribute('y1',y); line.setAttribute('y2',y); line.setAttribute('class','gridline'); svg.append(line); });
    const path = document.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d', points.map((p,i)=>`${i?'L':'M'}${p[0]},${p[1]}`).join(' ')); path.setAttribute('class','history-line'); svg.append(path);
    points.forEach(p => { const c = document.createElementNS(svg.namespaceURI,'circle'); c.setAttribute('cx',p[0]); c.setAttribute('cy',p[1]); c.setAttribute('r',5); c.setAttribute('class','history-point'); const title = document.createElementNS(svg.namespaceURI,'title'); title.textContent = p[2].toFixed(1); c.append(title); svg.append(c); }); root.replaceChildren(svg);
  },
  sources(items) { const root = document.getElementById('source-grid'); root.replaceChildren(...items.map(item => { const link = document.createElement('a'); link.href=item.url; link.target='_blank'; link.rel='noopener'; const name=document.createElement('b'); name.textContent=item.name; const role=document.createElement('span'); role.textContent=item.role; link.append(name,role); return link; })); },
  emptyRow(body, span, message) { const row=document.createElement('tr'); const cell=document.createElement('td'); cell.colSpan=span; cell.className='empty'; cell.textContent=message; row.append(cell); body.replaceChildren(row); },
  emptyBlock(root,message){const p=document.createElement('p');p.className='empty';p.textContent=message;root.replaceChildren(p);},
  text(id,value){const el=document.getElementById(id);if(el)el.textContent=value;},
  date(value){const d=new Date(value);return Number.isFinite(d.getTime())?new Intl.DateTimeFormat('en-GB',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'UTC',timeZoneName:'short'}).format(d):'—';},
  error(error){document.body.dataset.status='severe';this.text('mode','Unavailable');this.text('status-strip',`Dashboard data unavailable: ${error.message}. No stale value is presented as live.`);this.text('headline','Current warning could not be validated.');},
};
ui.init();
