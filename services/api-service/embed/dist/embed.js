"use strict";(()=>{function Z(e){var x;let t=f=>{var u;return(u=e==null?void 0:e.getAttribute(f))!=null?u:""},n=window,a=(x=n.__EMBED_CONFIG)!=null?x:n.EMBED_CONFIG,r=(f,u)=>{var h;return t(u)||(a?String((h=a[f])!=null?h:""):"")},i=r("agent","data-agent");i||console.error("[Helperium Widget] Missing data-agent attribute");let o=r("lang","data-lang"),c=navigator.language.startsWith("ru")?"ru":"en";return Object.freeze({agent:i,apiBase:r("apiBase","data-api-base")||window.location.origin,title:r("title","data-title")||"Assistant",greeting:r("greeting","data-greeting")||"How can I help?",accent:r("accent","data-accent")||"#0f766e",position:r("position","data-position")==="left"?"left":"right",lang:o==="ru"||o==="en"?o:c,placeholder:r("placeholder","data-placeholder")||"Ask a question...",width:r("width","data-width")||"min(380px, calc(100vw - 28px))",height:r("height","data-height")||"min(620px, calc(100vh - 44px))",triggerOffsetBottom:r("triggerOffsetBottom","data-trigger-offset-bottom")||"16px",headerColor:r("headerColor","data-header-color"),showHeader:r("showHeader","data-show-header")!=="false",botBubbleColor:r("botBubbleColor","data-bot-bubble-color")||"#eef3f4",botBubbleText:r("botBubbleText","data-bot-bubble-text")||"var(--ink)",voiceInput:r("voiceInput","data-voice-input")!=="false",voiceOutput:r("voiceOutput","data-voice-output")!=="false",voiceToggle:r("voiceToggle","data-voice-toggle")==="classic"?"classic":"telegram"})}var Q=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
</svg>
`;var tt=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <line x1="6" y1="12" x2="18" y2="12"/>
</svg>
`;var et=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <line x1="22" y1="2" x2="11" y2="13"/>
  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
</svg>
`;var nt=`<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="3" width="4" height="8" rx="2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M7 11a5 5 0 0 0 10 0" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M12 16v3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M9 19h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
</svg>
`;var at=`<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="3" width="4" height="8" rx="2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M7 11a5 5 0 0 0 10 0" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M12 16v3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M9 19h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
</svg>
`;var k={chat:Q,close:tt,send:et,mic:nt,micOff:at,thinking:'<div class="at-thinking-dots"><span></span><span></span><span></span></div>'};function M(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;")}function _(e){try{let n=sessionStorage.getItem(e);if(n)return n}catch(n){}let t=typeof crypto!="undefined"&&crypto.randomUUID?crypto.randomUUID():"sess-"+Date.now()+"-"+Math.random().toString(36).slice(2,10);try{sessionStorage.setItem(e,t)}catch(n){}return t}function rt(e){try{let t=sessionStorage.getItem(e);if(!t)return[];let n=JSON.parse(t);return Array.isArray(n)?n:[]}catch(t){return[]}}function it(e,t){try{sessionStorage.setItem(e,JSON.stringify(t))}catch(n){}}function N(e,t){function n(){return rt(e).filter(i=>i.sessionId===t).map(i=>({kind:i.kind,text:i.text,tools:i.tools||[]}))}function a(r,i,o){let c=rt(e);c.push({sessionId:t,kind:r,text:String(i||""),tools:o||[],ts:Date.now()});let x=c.filter(f=>f.sessionId===t);if(x.length>100){let f=x.length-100,u=0,h=c.filter(w=>w.sessionId===t&&u<f?(u++,!1):!0);it(e,h);return}it(e,c)}return{readStored:n,appendStored:a}}function T(e,t,n){let a=document.createElement(e);return a.className=t,n!==void 0&&(a.innerHTML=n),a}function V(){return!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia)}function ot(e,t){let n=t.position==="left"?"at-left":"at-right",a=T("button","at-trigger "+n,k.chat);e.appendChild(a);let r=T("div","at-panel "+n+" at-hidden");e.appendChild(r);let i=T("div","at-head"),o=T("div","at-head-info");o.innerHTML="<strong>"+M(t.title)+"</strong><span>"+M(t.agent)+"</span>";let c=T("div","at-head-status");c.innerHTML='<span class="at-dot"></span> '+(t.lang==="ru","Online"),o.appendChild(c);let x=T("button","at-close",k.close);i.appendChild(o),i.appendChild(x),r.appendChild(i);let f=T("div","at-messages");r.appendChild(f);let u=document.createElement("form");u.className="at-form";let h=document.createElement("textarea");h.rows=1,h.placeholder=t.placeholder,h.style.height="38px";let w=T("button","at-mic-btn",k.mic);w.type="button",w.title=t.lang==="ru"?"\u0417\u0430\u0436\u043C\u0438\u0442\u0435 \u0434\u043B\u044F \u0437\u0430\u043F\u0438\u0441\u0438":"Hold to record";let m=T("button","at-send-btn",k.send);m.type="submit";let l=t.voiceToggle==="telegram"&&t.voiceInput&&V(),g=T("div","at-swap-btn "+(l?"at-show-mic":"at-show-send"));g.appendChild(w),g.appendChild(m),!l&&t.voiceInput&&V()&&g.classList.add("at-legacy"),(!t.voiceInput||!V())&&(w.style.display="none");let v=T("div","at-form-row");v.appendChild(h),v.appendChild(g),u.appendChild(v);let E=T("div","at-mic-timer");return u.insertBefore(E,v),r.appendChild(u),{trigger:a,panel:r,messages:f,form:u,textarea:h,closeBtn:x,sendBtn:m,head:i,micBtn:w,micTimer:E,swapBtn:g}}function W(e){return e.classList.contains("at-msg-row")?e.querySelector(".at-msg")||e:(e.classList.contains("at-msg"),e)}function st(e){let t=e.closest(".at-msg-row");if(t){t.remove();return}e.remove()}function S(e){e&&(e.scrollTop=e.scrollHeight)}function lt(e){return!!(e&&e.scrollHeight-e.scrollTop-e.clientHeight<48)}async function $(e,t,n,a="en"){var c;let r=e.body.getReader(),i=new TextDecoder,o="";for(;;){let{done:x,value:f}=await r.read();if(x)break;o+=i.decode(f,{stream:!0});let u=o.split(`

`);o=u.pop();for(let h of u){let w=h.split(`
`).find(l=>l.startsWith("data:"));if(!w)continue;let m;try{m=JSON.parse(w.slice(5).trim())}catch(l){continue}switch(t.classList.contains("at-thinking")&&t.classList.remove("at-thinking"),m.type){case"token":n.onToken(m.text||"");break;case"final":n.onFinal(m.text||"");break;case"tool_call":{let g=JSON.parse(t.dataset.tools||"[]"),v=JSON.parse(t.dataset.displayNames||"{}");m.name&&!g.includes(m.name)&&(g.push(m.name),t.dataset.tools=JSON.stringify(g)),m.display_name&&m.name&&!v[m.name]&&(v[m.name]=m.display_name,t.dataset.displayNames=JSON.stringify(v)),n.onToolCall(m.name||"",m.display_name);break}case"audio":m.data&&n.onAudio(m.data);break;case"done":if(t.classList.contains("at-error"))return;(c=t.dataset.raw)!=null&&c.trim()||n.onFinal(a==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442.":"No response.");let l=[];try{l=JSON.parse(t.dataset.tools||"[]")}catch(g){}n.onDone(t.dataset.raw||"",l),t.dataset.saved="true";break;case"error":t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent=m.text||(a==="ru"?"\u041F\u0440\u043E\u0438\u0437\u043E\u0448\u043B\u0430 \u043E\u0448\u0438\u0431\u043A\u0430.":"An error occurred.");break}}}}function j(e){let{message:t,targetNode:n,config:a,sessionId:r,messagesEl:i,retryAttempts:o,maxRetries:c,callbacks:x,addMessage:f,removeMsgRow:u,scheduleRetry:h,retryChat:w,scrollToBottom:m}=e;n.classList.add("at-thinking");let l=a.apiBase+"/api/chat/"+encodeURIComponent(a.agent);fetch(l,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:t,session_id:r})}).then(g=>{if(g.status===429){n.classList.remove("at-thinking"),u(n);let v=g.headers.get("Retry-After"),E=5;if(v){let y=parseInt(v,10);!isNaN(y)&&y>0&&(E=y)}if(o.set(t,(o.get(t)||0)+1),o.get(t)>=c){let y=document.createElement("div");y.className="at-msg at-assistant at-error",y.innerHTML="\u26A0\uFE0F Server overloaded.";let H=document.createElement("button");H.className="at-retry-btn",H.textContent="Retry",y.appendChild(H),i.appendChild(y),m(i),H.addEventListener("click",()=>{o.delete(t),y.remove(),w(t)});return}let C=document.createElement("div");C.className="at-msg at-assistant",C.textContent="\u26A0\uFE0F "+(a.lang==="ru"?"\u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Server overloaded. Retry in")+" "+E+"s.",i.appendChild(C),m(i),h(t,E*1e3);return}if(!g.ok){n.classList.remove("at-thinking"),n.classList.add("at-error"),n.textContent="Error: "+g.status;return}return $(g,n,x,a.lang)}).catch(()=>{n.classList.remove("at-thinking"),n.classList.add("at-error"),n.innerHTML="\u26A0\uFE0F "+(a.lang==="ru"?"\u041D\u0435\u0442 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C.":"No connection to server.")+'<br><button class="at-retry-btn">'+(a.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C":"Retry")+"</button>";let g=n.querySelector(".at-retry-btn");g&&g.addEventListener("click",()=>{n.classList.remove("at-error"),n.innerHTML=k.thinking,j(e)})})}var ct=120,St="audio/webm;codecs=opus",Mt="audio/webm";function pt(){return{mediaRecorder:null,micChunks:[],micStream:null,micStartTime:0,micDuration:0,micTimerInterval:null}}function dt(e,t){e.micDuration=Math.floor((Date.now()-e.micStartTime)/1e3);let n=Math.floor(e.micDuration/60),a=e.micDuration%60;t.textContent=(n>0?n+"m ":"")+a+"s"}function I(e,t){if(!(e.mediaRecorder&&e.mediaRecorder.state==="recording")){if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){t.micBtn.classList.add("at-mic-disabled");return}navigator.mediaDevices.getUserMedia({audio:!0}).then(n=>{e.micStream=n,e.micChunks=[];let a=St;MediaRecorder.isTypeSupported(a)||(a=Mt),e.mediaRecorder=new MediaRecorder(n,{mimeType:a}),e.mediaRecorder.ondataavailable=r=>{r.data.size>0&&e.micChunks.push(r.data)},e.mediaRecorder.onstop=()=>{n.getTracks().forEach(c=>c.stop()),e.micStream=null,t.micBtn.innerHTML=k.mic,t.micBtn.classList.remove("at-mic-recording"),t.micTimer.classList.remove("at-mic-timer-visible"),e.micTimerInterval&&(clearInterval(e.micTimerInterval),e.micTimerInterval=null);let r=new Blob(e.micChunks,{type:a});if(r.size===0)return;let i=t.micTimer.textContent||e.micDuration+"s";t.addMessage("user","\u{1F3A4} "+i,{persist:!0});let o=t.addMessage("assistant","",{thinking:!0,persist:!1,scroll:!1});t.onStreamVoice(r,o)},e.mediaRecorder.start(),t.micBtn.innerHTML=k.micOff,t.micBtn.classList.add("at-mic-recording"),t.micTimer.classList.add("at-mic-timer-visible"),e.micStartTime=Date.now(),e.micDuration=0,dt(e,t.micTimer),e.micTimerInterval=window.setInterval(()=>dt(e,t.micTimer),1e3),ct>0&&setTimeout(()=>{e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()},ct*1e3)}).catch(n=>{n.name==="NotAllowedError"||n.name==="PermissionDeniedError"?t.addMessage("assistant",t.config.lang==="ru"?"\u274C \u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u0435 \u0434\u043E\u0441\u0442\u0443\u043F \u043A \u043C\u0438\u043A\u0440\u043E\u0444\u043E\u043D\u0443 \u0432 \u043D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0430\u0445 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0430":"\u274C Please allow microphone access in browser settings",{persist:!1}):t.micBtn.classList.add("at-mic-disabled")})}}function U(e){e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()}function gt(e,t,n){t.classList.add("at-thinking");let a=n.config.apiBase+"/api/chat/voice",r=new FormData;r.append("audio",e,"voice.webm"),r.append("session_id",n.sessionId),r.append("agent",n.config.agent),r.append("lang",n.config.lang),fetch(a,{method:"POST",body:r}).then(i=>{if(i.status===429){t.classList.remove("at-thinking"),t.remove();let o=document.createElement("div");o.className="at-msg at-assistant",o.textContent=n.config.lang==="ru"?"\u26A0\uFE0F \u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u043F\u043E\u0437\u0436\u0435.":"\u26A0\uFE0F Server overloaded. Try again later.",n.messagesEl.appendChild(o),n.scrollToBottom(n.messagesEl);return}if(!i.ok){t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent="Error: "+i.status;return}return $(i,t,n.callbacks,n.config.lang)}).catch(()=>{t.classList.remove("at-thinking"),t.classList.add("at-error"),t.innerHTML="\u26A0\uFE0F "+(n.config.lang==="ru"?"\u041E\u0448\u0438\u0431\u043A\u0430 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F.":"Connection error.")})}function Y(e){try{let t=atob(e),n=new Uint8Array(t.length);for(let o=0;o<t.length;o++)n[o]=t.charCodeAt(o);let a=new Blob([n],{type:"audio/mpeg"}),r=URL.createObjectURL(a);new Audio(r).play().catch(()=>{})}catch(t){}}function mt(e){let t=e.toLowerCase();return/поиск|найти|find|search/i.test(t)?"\u{1F50D}":/чтение|get|получени/i.test(t)?"\u{1F4CB}":/запрос|query/i.test(t)?"\u{1F4CA}":/list|список/i.test(t)?"\u{1F4CB}":"\u26A1"}function A(e,t){let n=t||{},a=[...new Set(e)];if(!a.length)return null;let r=document.createElement("div");return r.className="at-tool-strip",r.innerHTML=a.map(i=>{let o=n[i]||i;return`<span>${mt(o)} ${M(o)}</span>`}).join(""),r}function J(e,t,n,a){var x;let r=[...new Set(t)];if(!r.length)return;let i=((x=e.closest)==null?void 0:x.call(e,".at-msg-row"))||e,o=i.previousElementSibling;if(o&&o.className==="at-tool-strip"){o.innerHTML=r.map(f=>{let u=n[f]||f;return`<span>${mt(u)} ${M(u)}</span>`}).join("");return}let c=A(r,n);c&&a.insertBefore(c,i)}function R(e){let t=[],n=(e||"").split(`
`),a=0;for(;a<n.length;){let r=n[a];if(ut(n,a)){let o=[];for(;a<n.length&&n[a].trim().charAt(0)==="|";)o.push(n[a]),a++;t.push(Ct(o));continue}if(/^\s*[-*]\s+/.test(r)){let o=[];for(;a<n.length&&/^\s*[-*]\s+/.test(n[a]);)o.push(n[a].replace(/^\s*[-*]\s+/,"")),a++;t.push("<ul>"+o.map(c=>"<li>"+D(c)+"</li>").join("")+"</ul>");continue}if(/^\s*\d+\.\s+/.test(r)){let o=[];for(;a<n.length&&/^\s*\d+\.\s+/.test(n[a]);)o.push(n[a].replace(/^\s*\d+\.\s+/,"")),a++;t.push("<ol>"+o.map(c=>"<li>"+D(c)+"</li>").join("")+"</ol>");continue}let i=[];for(;a<n.length&&n[a].trim()&&!ut(n,a)&&!/^\s*[-*]\s+/.test(n[a])&&!/^\s*\d+\.\s+/.test(n[a]);)i.push(n[a]),a++;i.length&&t.push("<p>"+D(i.join(`
`)).replace(/\n/g,"<br>")+"</p>"),a<n.length&&!n[a].trim()&&a++}return t.join("")}function ut(e,t){let n=e[t],a=e[t+1];return!n||!a?!1:n.trim().charAt(0)==="|"&&/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(a)}function Ct(e){let t=[];for(let r=0;r<e.length;r++){let i=e[r];if(/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(i))continue;let o=i.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(c=>c.trim());t.push(o)}if(!t.length)return"";let n=t[0],a=t.slice(1);return'<div class="at-table-wrap"><table><thead><tr>'+n.map(r=>"<th>"+D(r)+"</th>").join("")+"</tr></thead><tbody>"+a.map(r=>"<tr>"+r.map(i=>"<td>"+D(i)+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>"}function D(e){return M(e).replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>").replace(/\*([^*]+)\*/g,"<em>$1</em>").replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')}var Lt=20,Et=3;function P(e,t,n){let a=e.dataset.raw||"";e.dataset.raw=a+t,e.dataset.typewriterRunning?e.dataset.typewriterBuffer=(e.dataset.typewriterBuffer||"")+t:(e.dataset.typewriterRunning="1",e.dataset.typewriterBuffer=a+t,e.dataset.typewriterDisplayed=a,ft(e,n))}function ft(e,t){let n=e.dataset.typewriterBuffer||"",a=e.dataset.typewriterDisplayed||"";if(a.length>=n.length){e.dataset.typewriterRunning="",e.innerHTML=R(n),S(t);return}let r=Math.min(Et,n.length-a.length),i=n.slice(0,a.length+r);e.dataset.typewriterDisplayed=i;let o=R(i);if(i.length<n.length)e.classList.add("at-typing-cursor"),e.innerHTML=o;else{e.classList.remove("at-typing-cursor"),e.innerHTML=o,e.dataset.typewriterRunning="",S(t);return}lt(t)&&S(t),setTimeout(()=>ft(e,t),Lt)}function q(e,t){e.classList.remove("at-thinking"),e.dataset.raw=t,e.innerHTML=R(t)}function ht(e,t,n,a){let r=a||{};if(e==="assistant"){let i=document.createElement("div");i.className="at-msg-row";let o=document.createElement("div");o.className="at-avatar",o.textContent="AI";let c=document.createElement("div");c.className="at-msg at-assistant",r.thinking?(c.dataset.raw="",c.innerHTML=k.thinking):(c.dataset.raw=t||"",c.innerHTML=R(t||"")),i.appendChild(c),i.appendChild(o),r.before?n.insertBefore(i,r.before):n.appendChild(i)}else{let i=document.createElement("div");i.className="at-msg at-user",i.textContent=t||"",r.before?n.insertBefore(i,r.before):n.appendChild(i)}return r.scroll!==!1&&S(n),n.lastElementChild}function G(e,t,n,a){let r=n();if(!r.length){a("assistant",e.greeting,{persist:!1,scroll:!1});return}t.innerHTML="";let i=[];for(let o of r)if(o.kind==="user")a("user",o.text,{persist:!1,scroll:!1});else if(o.kind==="assistant"){let c=(o.tools||[]).filter(Boolean),x=String(o.text||"");if(!x.trim()&&c.length>0){i=i.concat(c);continue}let f=i.concat(c);if(i=[],f.length>0){let m=A(f);m&&t.appendChild(m)}let u=document.createElement("div");u.className="at-msg-row";let h=document.createElement("div");h.className="at-avatar",h.textContent="AI";let w=document.createElement("div");w.className="at-msg at-assistant",w.dataset.raw=x,w.innerHTML=R(x),u.appendChild(w),u.appendChild(h),t.appendChild(u)}if(i.length>0){let o=A(i);o&&t.appendChild(o)}S(t)}var bt=`/*
 * Design Tokens
 *
 * Glassmorphism palette with frosted glass surfaces.
 * All visual variables in one place.
 */
:host {
  all: initial;
  --accent: #0f766e;
  --accent-rgb: 15, 118, 110;
  --accent-strong: #0b5f59;
  --accent-soft: #14b8a6;
  --ink: #0f172a;
  --ink-light: #475569;
  --muted: #94a3b8;
  --line: rgba(0, 0, 0, 0.06);
  --panel: rgba(255, 255, 255, 0.92);
  --panel-solid: #ffffff;
  --glass-bg: rgba(255, 255, 255, 0.72);
  --glass-border: rgba(255, 255, 255, 0.45);
  --glass-blur: 20px;
  --rose: #e11d48;
  --blue: #2563eb;
  --shadow-panel:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 10px 15px -3px rgba(0, 0, 0, 0.08),
    0 20px 50px -12px rgba(0, 0, 0, 0.15);
  --shadow-trigger:
    0 4px 14px rgba(var(--accent-rgb), 0.35),
    0 1px 3px rgba(0, 0, 0, 0.08);
  --radius: 12px;
  --radius-lg: 18px;
  --radius-xl: 20px;
  --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
  --ease-smooth: cubic-bezier(0.25, 0.1, 0.25, 1);
  --font: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

.at-root {
  all: initial;
  display: block;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.5;
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
/*
 * Root Container
 *
 * Base styles for the Shadow DOM root element.
 */
.at-root {
  all: initial;
  display: block;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.5;
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
/*
 * Trigger Button
 *
 * Floating glass-morphic action button.
 * Spring-animated entrance, pulse ring, hover lift.
 */
.at-trigger {
  position: fixed;
  bottom: 20px;
  width: 58px;
  height: 58px;
  border: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  color: white;
  cursor: pointer;
  box-shadow: var(--shadow-trigger);
  z-index: 2147483647;
  font-size: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.35s var(--ease-spring), box-shadow 0.3s var(--ease-smooth);
  padding: 0;
  outline: none;
  animation: at-trigger-in 0.6s var(--ease-spring) both;
  animation-delay: 0.3s;
}

/* Pulse ring \u2014 subtle breathing */
.at-trigger::after {
  content: "";
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0;
  animation: at-pulse-ring 3s ease-out infinite;
  animation-delay: 2s;
}

/* Hover: lift + glow */
.at-trigger:hover {
  transform: scale(1.1) translateY(-2px);
  box-shadow:
    0 6px 20px rgba(var(--accent-rgb), 0.4),
    0 2px 6px rgba(0, 0, 0, 0.1);
}

/* Active: press down */
.at-trigger:active {
  transform: scale(0.92);
  transition-duration: 0.1s;
}

/* Position variants */
.at-trigger.at-right { right: 20px; }
.at-trigger.at-left { left: 20px; }

/* SVG icon */
.at-trigger svg {
  width: 26px;
  height: 26px;
  position: relative;
  z-index: 1;
  transition: transform 0.2s var(--ease-spring);
}

.at-trigger:hover svg {
  transform: scale(1.05);
}
/*
 * Chat Panel
 *
 * Frosted glass panel with backdrop-blur.
 * Spring-animated open/close from bottom corner.
 */
.at-panel {
  position: fixed;
  bottom: 20px;
  width: min(400px, calc(100vw - 24px));
  height: min(640px, calc(100vh - 40px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--panel);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-panel);
  z-index: 2147483646;
  transition: opacity 0.3s var(--ease-smooth),
              transform 0.4s var(--ease-spring);
  transform-origin: bottom right;
}

/* Position variants */
.at-panel.at-right {
  right: 20px;
  transform-origin: bottom right;
}
.at-panel.at-left {
  left: 20px;
  transform-origin: bottom left;
}

/* Hidden state: scale down + fade */
.at-panel.at-hidden {
  opacity: 0;
  transform: translateY(16px) scale(0.92);
  pointer-events: none;
}

/* Visible state: spring up */
.at-panel:not(.at-hidden) {
  animation: at-panel-in 0.45s var(--ease-spring) both;
}
/*
 * Header
 *
 * Glass-morphic top bar with gradient accent.
 * Clean typography, subtle separator.
 */
.at-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 16px 16px 14px;
  background: linear-gradient(135deg,
    rgba(var(--accent-rgb), 0.95),
    rgba(var(--accent-rgb), 0.88));
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  color: white;
  flex-shrink: 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

/* Agent info */
.at-head-info strong {
  display: block;
  font-size: 15px;
  font-weight: 600;
  color: white;
  letter-spacing: -0.01em;
}

.at-head-info span {
  display: block;
  margin-top: 1px;
  font-size: 12px;
  opacity: 0.7;
  font-weight: 400;
}

/* Online status */
.at-head-status {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 2px;
  font-size: 11px;
  opacity: 0.8;
  font-weight: 500;
}

.at-head-status .at-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #34d399;
  box-shadow: 0 0 6px rgba(52, 211, 153, 0.5);
  animation: at-dot-pulse 2.5s ease-in-out infinite;
}

/* Close button */
.at-close {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(4px);
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0;
  transition: background 0.2s, transform 0.2s var(--ease-spring);
}

.at-close:hover {
  background: rgba(255, 255, 255, 0.25);
  transform: scale(1.08);
}

.at-close:active {
  transform: scale(0.92);
}

.at-close svg {
  width: 16px;
  height: 16px;
}
/*
 * Messages Area
 *
 * Scrollable container with frosted background.
 * Smooth message entrance, modern bubble design.
 */

/* Scrollable container */
.at-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: rgba(248, 250, 252, 0.5);
}

/* Custom scrollbar \u2014 thin, modern */
.at-messages::-webkit-scrollbar { width: 5px; }
.at-messages::-webkit-scrollbar-track { background: transparent; }
.at-messages::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.12);
  border-radius: 10px;
}
.at-messages::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.2);
}

/* \u2500\u2500\u2500 Message row (assistant with avatar) \u2500\u2500\u2500 */
.at-msg-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  margin-bottom: 2px;
  animation: at-msg-in 0.35s var(--ease-spring) both;
}

/* AI avatar \u2014 glass circle */
.at-avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 13px;
  font-weight: 700;
  margin-left: 14px;
  margin-top: -7px;
  position: relative;
  z-index: 1;
  border: 2px solid var(--panel-solid);
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.25);
}

/* \u2500\u2500\u2500 Base message bubble \u2500\u2500\u2500 */
.at-msg {
  min-width: 0;
  max-width: 88%;
  flex: 0 0 auto;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* User: right-aligned, accent gradient */
.at-msg.at-user {
  align-self: flex-end;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  color: white;
  border-radius: var(--radius) var(--radius) 4px var(--radius);
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.2);
  animation: at-msg-in 0.3s var(--ease-spring) both;
}

/* Assistant: left-aligned, frosted glass */
.at-msg.at-assistant {
  align-self: flex-start;
  background: var(--glass-bg);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid var(--glass-border);
  color: var(--ink);
  white-space: normal;
  margin-top: -2px;
  border-radius: var(--radius) var(--radius) var(--radius) 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

/* Thinking state */
.at-msg.at-assistant.at-thinking {
  padding: 16px 20px 14px;
  min-height: 40px;
  display: flex;
  align-items: center;
}

/* Error message */
.at-msg.at-error {
  background: rgba(254, 242, 242, 0.9);
  backdrop-filter: blur(8px);
  color: var(--rose);
  border: 1px solid rgba(254, 202, 202, 0.6);
  border-radius: var(--radius);
  font-size: 13px;
}

/* \u2500\u2500\u2500 Markdown inside assistant messages \u2500\u2500\u2500 */
.at-msg.at-assistant p { margin: 0 0 6px; }
.at-msg.at-assistant p:last-child,
.at-msg.at-assistant ul:last-child,
.at-msg.at-assistant ol:last-child { margin-bottom: 0; }
.at-msg.at-assistant ul,
.at-msg.at-assistant ol { margin: 0 0 10px; padding-left: 22px; }
.at-msg.at-assistant li { margin: 3px 0; }
.at-msg.at-assistant strong { font-weight: 700; }

.at-msg.at-assistant code {
  padding: 2px 6px;
  border-radius: 6px;
  background: rgba(var(--accent-rgb), 0.08);
  color: var(--accent-strong);
  font-size: 0.9em;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
}

.at-msg.at-assistant a {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.at-msg.at-assistant a:hover {
  color: var(--accent-strong);
}

/* \u2500\u2500\u2500 Thinking dots \u2500\u2500\u2500 */
.at-thinking-dots {
  display: flex;
  align-items: center;
  gap: 6px;
}

.at-thinking-dots span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent-soft);
  animation: at-dot-bounce 1.4s ease-in-out infinite both;
}

.at-thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
.at-thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
.at-thinking-dots span:nth-child(3) { animation-delay: 0s; }

/* Typing cursor */
.at-typing-cursor::after {
  content: "\u258C";
  display: inline;
  animation: at-cursor-blink 0.8s step-end infinite;
  color: var(--accent-soft);
  font-size: 14px;
  margin-left: 1px;
}
/*
 * Input Form
 *
 * Glass-morphic bottom bar with frosted textarea.
 * Smooth focus transitions, modern button design.
 */

/* Form container */
.at-form {
  padding: 8px 12px 12px;
  border-top: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  flex-shrink: 0;
}

/* Horizontal row */
.at-form-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

/* Textarea \u2014 glass input, matches button height */
.at-form textarea {
  flex: 1;
  resize: none;
  min-height: 38px;
  max-height: 120px;
  border: 1px solid var(--line);
  border-radius: 19px;
  padding: 8px 14px;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.45;
  outline: none;
  color: var(--ink);
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
  box-sizing: border-box;
}

.at-form textarea:focus {
  border-color: rgba(var(--accent-rgb), 0.5);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.08);
  background: rgba(255, 255, 255, 0.95);
}

.at-form textarea::placeholder {
  color: var(--muted);
  font-weight: 400;
}

/* Mic button */
.at-mic-btn {
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.04);
  color: var(--ink-light);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  flex-shrink: 0;
  transition: background 0.2s, color 0.2s, transform 0.25s var(--ease-spring);
}

.at-mic-btn:hover {
  background: rgba(0, 0, 0, 0.08);
  color: var(--ink);
  transform: scale(1.08);
}

/* Recording state */
.at-mic-btn.at-mic-recording {
  background: var(--rose);
  color: white;
  transform: scale(1.1);
  animation: at-mic-pulse 1.2s ease-in-out infinite;
}

/* Disabled */
.at-mic-btn.at-mic-disabled {
  opacity: 0.25;
  cursor: not-allowed;
  transform: none;
}

/* Send button \u2014 gradient accent */
.at-send-btn {
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  flex-shrink: 0;
  transition: transform 0.25s var(--ease-spring), opacity 0.2s, box-shadow 0.2s;
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.25);
}

.at-send-btn:hover {
  transform: scale(1.08);
  box-shadow: 0 4px 12px rgba(var(--accent-rgb), 0.35);
}

.at-send-btn:active {
  transform: scale(0.9);
  transition-duration: 0.1s;
}

.at-send-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.at-send-btn svg { width: 18px; height: 18px; }

/* Mic recording timer */
.at-mic-timer {
  text-align: center;
  color: var(--rose);
  font-size: 12px;
  font-weight: 700;
  padding: 4px 0 2px;
  letter-spacing: 0.03em;
  display: none;
}

.at-mic-timer-visible { display: block; }

/* \u2500\u2500\u2500 Telegram-style animated button swap \u2500\u2500\u2500 */

/* Container for the morphing mic/send button */
.at-swap-btn {
  position: relative;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
}

/* Both buttons stacked absolutely */
.at-swap-btn .at-mic-btn,
.at-swap-btn .at-send-btn {
  position: absolute;
  inset: 0;
  transition: transform 0.3s var(--ease-spring), opacity 0.25s ease;
}

/* Mic visible, send hidden */
.at-swap-btn.at-show-mic .at-mic-btn {
  transform: scale(1) rotate(0deg);
  opacity: 1;
  pointer-events: auto;
}
.at-swap-btn.at-show-mic .at-send-btn {
  transform: scale(0.3) rotate(-90deg);
  opacity: 0;
  pointer-events: none;
}

/* Send visible, mic hidden */
.at-swap-btn.at-show-send .at-send-btn {
  transform: scale(1) rotate(0deg);
  opacity: 1;
  pointer-events: auto;
}
.at-swap-btn.at-show-send .at-mic-btn {
  transform: scale(0.3) rotate(90deg);
  opacity: 0;
  pointer-events: none;
}

/* Hold-to-record visual feedback */
.at-swap-btn .at-mic-btn.at-mic-holding {
  transform: scale(1.15);
  background: var(--rose);
  color: white;
}

/* Legacy mode: both visible side by side (no swap, overrides at-show-*) */
.at-swap-btn.at-legacy .at-mic-btn,
.at-swap-btn.at-legacy .at-send-btn {
  position: static;
  transform: none;
  opacity: 1;
  pointer-events: auto;
}
.at-swap-btn.at-legacy {
  display: contents;
}
.at-swap-btn.at-legacy .at-mic-btn {
  display: flex;
}
.at-swap-btn.at-legacy .at-send-btn {
  display: flex;
}
/*
 * Tool Strip & Tables
 *
 * Tool call pills with glass effect.
 * Modern table styling with subtle borders.
 */

/* Tool call pills */
.at-tool-strip {
  align-self: flex-start;
  max-width: 92%;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 2px;
}

.at-tool-strip span {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 3px 10px;
  border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.06);
  border: 1px solid rgba(var(--accent-rgb), 0.1);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.01em;
  animation: at-msg-in 0.3s var(--ease-spring) both;
}

/* Markdown table */
.at-table-wrap {
  max-width: 100%;
  overflow-x: auto;
  margin: 10px 0 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.at-table-wrap table {
  min-width: 520px;
  width: 100%;
  border-collapse: collapse;
}

.at-table-wrap th,
.at-table-wrap td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  line-height: 1.4;
}

.at-table-wrap th {
  background: rgba(0, 0, 0, 0.02);
  color: var(--muted);
  font-weight: 600;
  text-align: left;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.at-table-wrap tr:last-child td { border-bottom: 0; }
.at-table-wrap tr:hover td { background: rgba(0, 0, 0, 0.015); }

/* Retry button */
.at-retry-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 8px 18px;
  border: 1px solid rgba(var(--accent-rgb), 0.3);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(8px);
  color: var(--accent);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  font-family: var(--font);
  outline: none;
  transition: all 0.2s var(--ease-smooth);
}

.at-retry-btn:hover {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(var(--accent-rgb), 0.25);
}

.at-retry-btn:active {
  transform: translateY(0);
}

/* Retry countdown */
.at-msg.at-retry-countdown {
  align-self: center;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
  max-width: 100%;
  font-weight: 500;
}
/*
 * Animations & Keyframes
 *
 * Spring-based, smooth animations for all interactive elements.
 */

/* Trigger button entrance \u2014 spring from below */
@keyframes at-trigger-in {
  0% {
    opacity: 0;
    transform: translateY(20px) scale(0.6);
  }
  100% {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* Panel entrance \u2014 spring from corner */
@keyframes at-panel-in {
  0% {
    opacity: 0;
    transform: translateY(16px) scale(0.92);
  }
  100% {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* Trigger pulse ring \u2014 slow breathing */
@keyframes at-pulse-ring {
  0% { transform: scale(1); opacity: 0.25; }
  50% { transform: scale(1.2); opacity: 0; }
  100% { transform: scale(1.2); opacity: 0; }
}

/* Status dot pulse */
@keyframes at-dot-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
}

/* Message entrance \u2014 slide up + fade */
@keyframes at-msg-in {
  0% {
    opacity: 0;
    transform: translateY(8px);
  }
  100% {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Thinking dots \u2014 wave bounce */
@keyframes at-dot-bounce {
  0%, 80%, 100% {
    transform: translateY(0) scale(0.75);
    opacity: 0.35;
  }
  40% {
    transform: translateY(-7px) scale(1);
    opacity: 0.85;
  }
}

/* Typing cursor blink */
@keyframes at-cursor-blink {
  50% { opacity: 0; }
}

/* Mic recording pulse */
@keyframes at-mic-pulse {
  0% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0.3); }
  70% { box-shadow: 0 0 0 10px rgba(225, 29, 72, 0); }
  100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0); }
}
/*
 * Responsive
 *
 * Full-screen takeover on mobile devices.
 */

@media (max-width: 480px) {
  .at-panel {
    width: 100vw !important;
    height: 100vh !important;
    bottom: 0 !important;
    right: 0 !important;
    left: 0 !important;
    border-radius: 0 !important;
    border: 0 !important;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .at-trigger { bottom: 14px; }
  .at-trigger.at-right { right: 14px; }
  .at-trigger.at-left { left: 14px; }

  .at-head { padding: 14px 14px 12px; }
  .at-messages { padding: 14px; }
  .at-form { padding: 8px 10px 10px; }
}
`;var Bt=3;function Rt(){let e=document.currentScript;if(e&&e instanceof HTMLScriptElement)return e;let t=document.querySelector("script[data-agent]");if(t)return t;let n=document.querySelector('script[src*="embed.js"]');return n||null}function Dt(e){let t=e.replace("#",""),n=parseInt(t.length===3?t.split("").map(a=>a+a).join(""):t,16);return`${n>>16&255}, ${n>>8&255}, ${n&255}`}function It(e){let t=e.headerColor||e.accent;return`:host {
  --accent: ${e.accent};
  --accent-rgb: ${Dt(e.accent)};
  --accent-strong: ${e.accent};
  --trigger-offset-bottom: ${e.triggerOffsetBottom};
  --panel-width: ${e.width};
  --panel-height: ${e.height};
  --header-bg: ${t};
  --bot-bubble-bg: ${e.botBubbleColor};
  --bot-bubble-text: ${e.botBubbleText};
}
${e.showHeader?"":".at-head { display: none; }"}`}function xt(){var K,X;let e=Rt(),t=Z(e);if(!t.agent)return;let n,a,r,i="at_messages_"+t.agent,o="at_session_"+t.agent;r=_(o),{readStored:n,appendStored:a}=N(i,r);let c=pt(),x=new Map,f=document.createElement("div");f.id="helperium-widget-"+t.agent.replace(/[^a-zA-Z0-9_-]/g,"");let u=f.attachShadow({mode:"open"}),h=document.createElement("style");h.textContent=bt,u.appendChild(h);let w=document.createElement("style");w.textContent=It(t),u.appendChild(w);let m=document.createElement("div");m.className="at-root",u.appendChild(m);let l=ot(m,t),g=l.messages;function v(d,p,s){let b=ht(d,p,g,s);return s!=null&&s.persist&&a(d,p,s.tools),b}function E(d,p){let s=Math.ceil(p/1e3),b=document.createElement("div");b.className="at-msg at-retry-countdown",b.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+s+"s...",g.appendChild(b),S(g);let L=setInterval(()=>{s--,s<=0?(clearInterval(L),b.remove(),O(d)):b.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+s+"s..."},1e3)}function O(d){let p=v("assistant","",{thinking:!0,persist:!1,scroll:!1}),s=W(p);C(d,s)}function C(d,p){j({message:d,targetNode:p,config:t,sessionId:r,messagesEl:g,retryAttempts:x,maxRetries:Bt,callbacks:{onToken:s=>P(p,s,g),onFinal:s=>q(p,s),onToolCall:(s,b)=>{let L=JSON.parse(p.dataset.tools||"[]"),B=JSON.parse(p.dataset.displayNames||"{}");L.includes(s)||(L.push(s),p.dataset.tools=JSON.stringify(L)),b&&!B[s]&&(B[s]=b,p.dataset.displayNames=JSON.stringify(B)),J(p,L,B,g)},onAudio:s=>Y(s),onDone:(s,b)=>{a("assistant",s,b)},onError:s=>{p.classList.remove("at-thinking"),p.classList.add("at-error"),p.textContent=s}},addMessage:v,removeMsgRow:st,scheduleRetry:(s,b)=>E(s,b),retryChat:O,scrollToBottom:S})}l.trigger.addEventListener("click",()=>{l.panel.classList.remove("at-hidden"),l.trigger.style.display="none",l.textarea.focus(),S(g)}),l.closeBtn.addEventListener("click",()=>{l.panel.classList.add("at-hidden"),l.trigger.style.display="flex"});function y(){let d=l.textarea.value.trim();if(!d)return;l.textarea.value="",l.textarea.style.height="auto",H(),v("user",d,{persist:!0});let p=v("assistant","",{thinking:!0,persist:!1,scroll:!1}),s=W(p);C(d,s)}l.textarea.addEventListener("input",function(){this.style.height="auto",this.style.height=Math.min(this.scrollHeight,120)+"px",H()}),l.textarea.addEventListener("keydown",d=>{d.key==="Enter"&&!d.shiftKey&&(d.preventDefault(),y())}),l.form.addEventListener("submit",d=>{d.preventDefault(),y()});function H(){if(t.voiceToggle!=="telegram"||!z)return;l.textarea.value.trim().length>0?(l.swapBtn.classList.remove("at-show-mic"),l.swapBtn.classList.add("at-show-send")):(l.swapBtn.classList.remove("at-show-send"),l.swapBtn.classList.add("at-show-mic"))}let z=t.voiceInput&&typeof((K=navigator.mediaDevices)==null?void 0:K.getUserMedia)=="function";if(t.voiceToggle==="telegram"&&z){let d=!1;l.micBtn.addEventListener("mousedown",s=>{s.preventDefault(),!d&&(d=!0,l.micBtn.classList.add("at-mic-holding"),I(c,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:r,addMessage:v,onStreamVoice:F(),onStreamChat:C}))});let p=()=>{d&&(d=!1,l.micBtn.classList.remove("at-mic-holding"),U(c))};l.micBtn.addEventListener("mouseup",p),l.micBtn.addEventListener("mouseleave",p),l.micBtn.addEventListener("touchstart",s=>{s.preventDefault(),!d&&(d=!0,l.micBtn.classList.add("at-mic-holding"),I(c,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:r,addMessage:v,onStreamVoice:F(),onStreamChat:C}))},{passive:!1}),l.micBtn.addEventListener("touchend",p),l.micBtn.addEventListener("touchcancel",p)}else z&&(l.micBtn.style.display="flex",l.micBtn.addEventListener("mousedown",d=>{d.preventDefault(),l.micBtn.classList.contains("at-mic-recording")?U(c):I(c,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:r,addMessage:v,onStreamVoice:F(),onStreamChat:C})}));function F(){return(d,p)=>{gt(d,p,{config:t,sessionId:r,messagesEl:g,callbacks:{onToken:s=>P(p,s,g),onFinal:s=>q(p,s),onToolCall:(s,b)=>{J(p,[s],b?{[s]:b}:{},g)},onAudio:s=>Y(s),onDone:(s,b)=>{a("assistant",s,b)},onError:s=>{p.classList.remove("at-thinking"),p.classList.add("at-error"),p.textContent=s}},scrollToBottom:S})}}G(t,g,n,v),document.body.appendChild(f),window.__agentTutorSetAgent=d=>{if(!d)return;t.agent=d;let p="at_messages_"+d,s="at_session_"+d,b=_(s),L=N(p,b);n=L.readStored,a=L.appendStored,r=b;let B=l.head.querySelector(".at-head-info");B&&(B.innerHTML="<strong>"+M(t.title)+"</strong><span>"+M(d)+"</span>"),g.innerHTML="",G(t,g,n,v);try{localStorage.setItem("agentTutorAgentId",d)}catch(At){}};try{let d=localStorage.getItem("agentTutorAgentId");d&&t.agent!==d&&((X=window.__agentTutorSetAgent)==null||X.call(window,d))}catch(d){}}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",xt):xt();})();
//# sourceMappingURL=embed.js.map
