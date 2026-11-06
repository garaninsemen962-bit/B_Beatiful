/* Offline Python editor. Uses the real textarea for selection, input and form submission.
 * Highlight markup is escaped; no contenteditable, eval, remote library, or CDN.
 */
(function(root){
 'use strict';
 const esc=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const keywords=new Set('False None True and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield'.split(' '));
 const builtins=new Set('abs all any bool bytes callable dict enumerate filter float format getattr hasattr int isinstance len list map max min next object open print range repr reversed round set sorted str sum super tuple type zip Exception ValueError RuntimeError'.split(' '));
 function highlight(code){
  if(code.length>200000)return esc(code);
  const out=[];let i=0;const number=/(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?[\d_]+)?[jJ]?)/y;
  const emit=(kind,text)=>out.push(kind?'<span class="py-'+kind+'">'+esc(text)+'</span>':esc(text));
  while(i<code.length){
   const start=i,c=code[i];
   if(c==='#'){while(i<code.length&&code[i]!='\n')i++;emit('comment',code.slice(start,i));continue}
   let quoteAt=i;
   if(/[rRuUbBfF]/.test(c)){
    let j=i;while(j<code.length&&j-i<2&&/[rRuUbBfF]/.test(code[j]))j++;
    if(code[j]==='"'||code[j]==="'")quoteAt=j;
   }
   if(code[quoteAt]==='"'||code[quoteAt]==="'"){
    const q=code[quoteAt],triple=code.slice(quoteAt,quoteAt+3)===q.repeat(3),mark=triple?q.repeat(3):q;
    i=quoteAt+mark.length;
    while(i<code.length){
     if(code[i]==='\\'){i=Math.min(code.length,i+2);continue}
     if(code.slice(i,i+mark.length)===mark){i+=mark.length;break}
     if(!triple&&code[i]==='\n')break;
     i++;
    }
    emit('string',code.slice(start,i));continue;
   }
   if(/[A-Za-z_\u0400-\u04ff]/.test(c)){
    i++;while(i<code.length&&/[\w\u0400-\u04ff]/.test(code[i]))i++;
    const word=code.slice(start,i);emit(keywords.has(word)?'keyword':builtins.has(word)?'builtin':'',word);continue;
   }
   if(/\d/.test(c)||(c==='.'&&/\d/.test(code[i+1]||''))){number.lastIndex=i;const m=number.exec(code);if(m){i=number.lastIndex;emit('number',m[0]);continue}}
   emit('',''+c);i++;
  }
  return out.join('');
 }
 // Pure editing operations are also exercised by node tests.
 function indent(text,start,end,outdent=false){
  const lineStart=text.lastIndexOf('\n',Math.max(0,start-1))+1;
  const first=start===0?0:lineStart;
  if(!outdent&&start===end){const column=start-first,spaces=' '.repeat(4-column%4);return {text:text.slice(0,start)+spaces+text.slice(end),start:start+spaces.length,end:start+spaces.length}}
  let last=end;
  if(end>start&&text[end-1]==='\n')last=end-1;
  const tail=text.indexOf('\n',last),blockEnd=tail<0?text.length:tail;
  let pos=first,delta=0,newStart=start,newEnd=end;
  const lines=text.slice(first,blockEnd).split('\n').map(line=>{
   let remove=0,add='';
   if(outdent){if(line.startsWith('\t'))remove=1;else remove=Math.min(4,(line.match(/^ */)||[''])[0].length)}else add='    ';
   const change=add.length-remove;
   if(pos<=start)newStart+=add.length-Math.min(remove,Math.max(0,start-pos));
   if(pos<end||start===end)newEnd+=add.length-Math.min(remove,Math.max(0,end-pos));
   delta+=change;pos+=line.length+1;return add+line.slice(remove);
  });
  return {text:text.slice(0,first)+lines.join('\n')+text.slice(blockEnd),start:Math.max(first,newStart),end:Math.max(first,newEnd)};
 }
 function newline(text,start,end){
  const beginning=start===0?0:text.lastIndexOf('\n',start-1)+1,before=text.slice(beginning,start),padding=(before.match(/^[ \t]*/)||[''])[0];
  const extra=before.trimEnd().endsWith(':')?'    ':'';const insert='\n'+padding+extra;
  return {text:text.slice(0,start)+insert+text.slice(end),start:start+insert.length,end:start+insert.length};
 }
 function mount(textarea){
  const wrapper=document.createElement('div');wrapper.className='python-editor';
  const gutter=document.createElement('div');gutter.className='code-gutter';gutter.setAttribute('aria-hidden','true');const numbers=document.createElement('pre');gutter.append(numbers);
  const surface=document.createElement('div');surface.className='code-surface';const layer=document.createElement('pre');layer.className='code-highlight';layer.setAttribute('aria-hidden','true');
  const status=document.createElement('div');status.className='editor-status';status.setAttribute('role','status');
  textarea.parentNode.insertBefore(wrapper,textarea);wrapper.append(gutter,surface);surface.append(layer,textarea);wrapper.after(status);
  textarea.classList.add('code-input');textarea.setAttribute('wrap','off');textarea.spellcheck=false;
  let frame=0;
  const scroll=()=>{layer.style.transform=`translate(${-textarea.scrollLeft}px, ${-textarea.scrollTop}px)`;numbers.style.transform=`translateY(${-textarea.scrollTop}px)`};
  const position=()=>{const prefix=textarea.value.slice(0,textarea.selectionStart),parts=prefix.split('\n');status.textContent=`Python · Строка ${parts.length}, столбец ${parts.at(-1).length+1} · Отступ: 4 пробела`+(textarea.value.length>200000?' · Подсветка отключена для большого файла':'')};
  function render(){layer.innerHTML=highlight(textarea.value)+'\n';numbers.textContent=Array.from({length:textarea.value.split('\n').length},(_,i)=>i+1).join('\n')+'\n';scroll();position()}
  function update(){cancelAnimationFrame(frame);frame=requestAnimationFrame(render)}
  function apply(edit){
   // Only replace the changed range, preserving native undo where the browser supports it.
   const old=textarea.value;let a=0,b=0;
   while(a<old.length&&a<edit.text.length&&old[a]===edit.text[a])a++;
   while(b<old.length-a&&b<edit.text.length-a&&old[old.length-1-b]===edit.text[edit.text.length-1-b])b++;
   textarea.focus();textarea.setSelectionRange(a,old.length-b);
   const insert=edit.text.slice(a,edit.text.length-b);let native=false;
   try{native=document.execCommand('insertText',false,insert)}catch{}
   if(!native)textarea.setRangeText(insert,a,old.length-b,'end');
   textarea.setSelectionRange(edit.start,edit.end);textarea.dispatchEvent(new Event('input',{bubbles:true}));render();
  }
  textarea.addEventListener('keydown',e=>{
   if(e.isComposing)return;
   if(e.key==='Tab'){e.preventDefault();apply(indent(textarea.value,textarea.selectionStart,textarea.selectionEnd,e.shiftKey))}
   else if(e.key==='Enter'&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();apply(newline(textarea.value,textarea.selectionStart,textarea.selectionEnd))}
   else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();textarea.form.requestSubmit()}
   else if(e.key==='Backspace'&&textarea.selectionStart===textarea.selectionEnd){
    const pos=textarea.selectionStart,first=pos===0?0:textarea.value.lastIndexOf('\n',pos-1)+1,before=textarea.value.slice(first,pos);
    if(before&&/^ +$/.test(before)){e.preventDefault();const remove=before.length%4||4;apply({text:textarea.value.slice(0,pos-remove)+textarea.value.slice(pos),start:pos-remove,end:pos-remove})}
   }
   else if(e.key==='Escape'){textarea.blur()}
  });
  textarea.addEventListener('input',update);textarea.addEventListener('scroll',scroll);for(const ev of ['click','keyup','select'])textarea.addEventListener(ev,position);
  render();return {render,setValue(value){textarea.value=value;render()}};
 }
 const api={highlight,indent,newline,mount};if(typeof module!=='undefined'&&module.exports)module.exports=api;if(root)root.DDPythonEditor=api;
})(typeof window!=='undefined'?window:null);
