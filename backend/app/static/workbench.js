const $ = id => document.getElementById(id);
const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if (className) el.className = className; return el; };
const money = value => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:0}).format(Number(value));
function line(parent, label, value) { const row = text('p', '', 'line'); row.append(text('strong', label + ' '), document.createTextNode(value)); parent.append(row); }
$('search-form').addEventListener('submit', async event => {
  event.preventDefault(); $('submit').disabled = true; $('output').hidden = true; $('status').textContent = 'Executing search…';
  try {
    const mode = $('mode').value;
    const execution = mode === 'serial' ? {mode} : {mode,workers:Number($('workers').value),allow_fallback:mode==='auto'};
    const response = await fetch(`/api/v1/hpc/catalogues/${$('dataset').value}/search`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:$('query').value,execution})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    $('raw').textContent = JSON.stringify(data,null,2);
    $('results').replaceChildren();
    for (const [index,row] of data.recommendations.entries()) {
      const p = row.product, card = text('article','','product');
      card.append(text('div',`RANK 0${index+1} / ${p.category.toUpperCase()}`,'rank'),text('h3',p.name),text('div',money(p.price),'price'),text('div',`${p.stock} in stock · eligible · fictional catalogue product`,'metadata'),text('p',row.reason,'reason'));
      $('results').append(card);
    }
    if (!data.recommendations.length) $('results').append(text('p',data.adequacy.reason,'line'));
    $('summary').textContent = `${data.recommendations.length} shown / ${data.eligible_count.toLocaleString()} eligible / ${data.dataset.count.toLocaleString()} products`;
    $('constraints').replaceChildren();
    line($('constraints'),'Parsed roles',data.intent.role_clauses.flatMap(c=>c.roles).join(', ') || 'No controlled role resolved');
    line($('constraints'),'Hard price cap',data.intent.strict_max_price ? money(data.intent.strict_max_price) : 'None requested');
    line($('constraints'),'Availability','Active, unarchived, in-stock products only');
    line($('constraints'),'Adequacy',data.adequacy.reason);
    line($('constraints'),'Excluded',JSON.stringify(data.filtered_counts));
    const e = data.execution;
    $('metrics').replaceChildren();
    const metrics = [['Actual mode',e.actual_mode],['Workers',e.worker_count ?? 0],['Feature wall',`${(e.feature_wall_ms??0).toFixed(1)} ms`],['Admission queue',`${(e.admission_queue_ms??0).toFixed(1)} ms`],['Pool startup',`${(e.startup_ms??0).toFixed(1)} ms`],['Global fusion',`${(e.fusion_ms??0).toFixed(1)} ms`]];
    for (const [label,value] of metrics) { const m=text('div','','metric');m.append(text('span',label),text('b',String(value)));$('metrics').append(m); }
    line($('metrics').parentElement,'Executing PIDs',(e.executing_pids || []).join(', ') || 'Coordinator only');
    // Replace the previous PID row on subsequent requests.
    const pidRows=$('metrics').parentElement.querySelectorAll('.line');for(let i=0;i<pidRows.length-1;i++)pidRows[i].remove();
    $('status').textContent = `Completed · ${data.profile.total_ms.toFixed(1)} ms application observation · ${data.dataset.dataset_id}${e.fallback_reason ? ' · '+e.fallback_reason : ''}`;
    $('output').hidden = false;
  } catch (error) { $('status').textContent = `Search unavailable: ${error.message}. Generate the selected catalogue first; see README setup.`; }
  finally { $('submit').disabled = false; }
});
