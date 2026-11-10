
'use strict';
const $=id=>document.getElementById(id), video=$('video');
const localSaver=location.hostname==='127.0.0.1'&&location.port==='8767';
const photoToken='__PHOTO_TOKEN__';
$('photoStatus').textContent=localSaver?'Photos save directly to this computer’s Desktop.':'For automatic Desktop photos, open http://127.0.0.1:8767/ on the computer running the app.';
let pc=null,session=null,abort=null,retry=null,watch=null,epoch=0,enabled=false,phase='ready';
let started=0,lastFrame=0,previous=null,everLive=false,inOutage=false,outageStart=0,outageMs=0,drops=0;
let samples=[],events=[],timeline=[],ip='192.168.2.3';
function log(message){events.push({time:new Date().toISOString(),message});if(events.length>1000)events.shift();$('events').textContent=events.slice(-12).map(x=>new Date(x.time).toLocaleTimeString()+'  '+x.message).reverse().join('\n');}
function setStatus(text,kind){$('status').textContent=text;$('status').className='status '+kind;}
function interrupted(){if(everLive&&!inOutage){inOutage=true;outageStart=lastFrame||performance.now();drops++;$('drops').textContent=drops;log('Video interrupted');}}
function clearConnection(){
  if(watch)clearInterval(watch);watch=null;
  if(abort)abort.abort();abort=null;
  if(pc){pc.close();pc=null;}
  if(session){fetch(session,{method:'DELETE',signal:AbortSignal.timeout(2000)}).catch(()=>{});session=null;}
  video.srcObject=null;previous=null;
}
function schedule(reason){
  if(!enabled||retry)return;
  epoch++;interrupted();clearConnection();phase='outage';
  setStatus('No video · retrying', 'bad');$('overlay').classList.remove('hidden');$('message').textContent='Video unavailable';$('detail').textContent=reason+' · retrying in 1 second';
  log(reason);retry=setTimeout(()=>{retry=null;connect();},1000);
}
async function connect(){
  if(!enabled)return;const token=++epoch;clearConnection();phase='connecting';started=performance.now();lastFrame=0;
  setStatus('Connecting…','');$('overlay').classList.remove('hidden');$('message').textContent='Connecting…';$('detail').textContent='Automatic reconnect is on.';
  const localPc=new RTCPeerConnection({iceServers:[]});pc=localPc;abort=new AbortController();const controller=abort;
  let endpoint='http://'+ip+':8889/usbtest/whep';
  localPc.addTransceiver('video',{direction:'recvonly'});
  localPc.ontrack=ev=>{if(token!==epoch)return;video.srcObject=ev.streams[0]||new MediaStream([ev.track]);video.play().catch(()=>{$('resume').hidden=false;log('Playback needs a click: use Resume video.');});};
  localPc.onconnectionstatechange=()=>{if(token===epoch&&localPc.connectionState==='failed')schedule('WebRTC connection failed');};
  watch=setInterval(()=>sample(token,localPc),1000);
  const timeout=setTimeout(()=>controller.abort(),3500);
  try{
    await localPc.setLocalDescription(await localPc.createOffer());
    if(localPc.iceGatheringState!=='complete')await new Promise(resolve=>{const t=setTimeout(resolve,2000);localPc.addEventListener('icegatheringstatechange',()=>{if(localPc.iceGatheringState==='complete'){clearTimeout(t);resolve();}});});
    if(token!==epoch)return;
    const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/sdp'},body:localPc.localDescription.sdp,signal:controller.signal});
    if(!response.ok)throw new Error('Stream response HTTP '+response.status);
    const location=response.headers.get('Location');const newSession=location?new URL(location,endpoint).href:null;
    if(token!==epoch){if(newSession)fetch(newSession,{method:'DELETE',signal:AbortSignal.timeout(2000)}).catch(()=>{});return;}
    session=newSession;
    const sdp=await response.text();
    // Only accept media via the Pi Ethernet address, never its MIT Wi-Fi address.
    const lines=sdp.split('\r\n'),candidates=lines.filter(l=>l.startsWith('a=candidate:'));
    const allowed=candidates.filter(l=>l.trim().split(/\s+/)[4]===ip);
    if(!allowed.length)throw new Error('MediaMTX did not advertise the Ethernet IP '+ip);
    const filtered=lines.filter(l=>!l.startsWith('a=candidate:')||allowed.includes(l)).join('\r\n');
    if(token!==epoch)return;
    await localPc.setRemoteDescription({type:'answer',sdp:filtered});
  }catch(error){if(token===epoch)schedule(error.name==='AbortError'?'Connection timed out':error.message);}
  finally{clearTimeout(timeout);}
}
async function sample(token,localPc){
  if(token!==epoch)return;
  try{
    const report=await localPc.getStats();if(token!==epoch)return;
    let selectedPair=null;
    report.forEach(s=>{if(s.type==='transport'&&s.selectedCandidatePairId)selectedPair=report.get(s.selectedCandidatePairId);});
    if(!selectedPair)report.forEach(s=>{if(s.type==='candidate-pair'&&s.state==='succeeded'&&s.nominated)selectedPair=s;});
    if(selectedPair){
      const remote=report.get(selectedPair.remoteCandidateId),local=report.get(selectedPair.localCandidateId);
      const remoteIP=remote&&(remote.address||remote.ip),localIP=local&&(local.address||local.ip);
      $('route').textContent=remoteIP?'Media route · '+(localIP||'this computer')+' → '+remoteIP:'Media endpoint · '+ip+' (browser hides candidate addresses)';
      if(remoteIP&&remoteIP!==ip){schedule('Refusing alternate media route '+remoteIP);return;}
    }
    let row=null;report.forEach(s=>{if(s.type==='inbound-rtp'&&(s.kind==='video'||s.mediaType==='video'))row=s;});
    const now=performance.now();let mbps=0,fps=0,loss=null;
    if(row){
      if(previous){const dt=(row.timestamp-previous.timestamp)/1000;if(dt>0){mbps=Math.max(0,(row.bytesReceived-previous.bytesReceived)*8/dt/1e6);fps=Math.max(0,((row.framesDecoded||0)-(previous.framesDecoded||0))/dt);}
        const lost=Math.max(0,(row.packetsLost||0)-(previous.packetsLost||0)),received=Math.max(0,(row.packetsReceived||0)-(previous.packetsReceived||0));if(lost+received>0)loss=100*lost/(lost+received);
      }
      if((previous&&row.framesDecoded>previous.framesDecoded)||(!previous&&row.framesDecoded>0))lastFrame=now;
      previous=row;
    }
    const age=lastFrame?(now-lastFrame)/1000:null;
    $('rate').textContent=mbps.toFixed(2)+' Mb/s';$('fps').textContent=fps.toFixed(0);$('loss').textContent=loss===null?'—':loss.toFixed(1)+'%';$('age').textContent=age===null?'—':age.toFixed(1)+' s';
    if(age!==null&&age<=2){
      if(phase!=='live'){
        if(inOutage){outageMs+=now-outageStart;log('Video recovered after '+((now-outageStart)/1000).toFixed(1)+' s');inOutage=false;}else log('Live video received');
      }
      everLive=true;phase='live';setStatus('LIVE · bridge video','live');$('overlay').classList.add('hidden');
    }else if(lastFrame&&age>2){
      interrupted();phase='outage';setStatus('VIDEO FROZEN','bad');$('overlay').classList.remove('hidden');$('message').textContent='Video frozen';$('detail').textContent='No new decoded frames · automatic retry pending';
    }
    samples.push({time:new Date().toISOString(),state:phase,mbps:+mbps.toFixed(3),fps:+fps.toFixed(1),packetLossPercent:loss,lastFrameAgeSec:age});if(samples.length>14400)samples.shift();
    if((lastFrame&&age>2.5)||(!lastFrame&&now-started>6500))schedule('No decoded video frames');
  }catch(error){if(token===epoch)schedule('Video statistics unavailable: '+error.message);}
}
function start(){
  const value=$('ip').value.trim();if(!/^(\d{1,3}\.){3}\d{1,3}$/.test(value)||value.split('.').some(n=>+n>255)){alert('Enter the Pi Ethernet IPv4 address.');return;}
  enabled=false;epoch++;if(retry)clearTimeout(retry);retry=null;clearConnection();
  if(inOutage){outageMs+=performance.now()-outageStart;inOutage=false;}
  ip=value;enabled=true;$('route').textContent='Bridge Ethernet only · '+ip;log('Connecting to Ethernet '+ip);connect();
}
$('start').onclick=start;
$('resume').onclick=async()=>{try{await video.play();$('resume').hidden=true;}catch(e){log('Playback: '+e.message);}};
video.addEventListener('playing',()=>{$('resume').hidden=true;});
let captureBusy=false;
// Grabs the current live frame and saves it. Extra headers carry map metadata (position, label, frame).
// Throws an Error (with .status for server rejections) when no picture could be saved.
async function captureFrame(headers={}){
  if(captureBusy)throw new Error('Another capture is in progress');
  if(!localSaver)throw new Error('Open http://127.0.0.1:8767/ for automatic Desktop saving.');
  if(video.paused){try{await video.play();}catch(e){throw new Error('Press Play on the video, then take a photo.');}}
  if(pc)await sample(epoch,pc);
  if(video.readyState<2||!lastFrame||performance.now()-lastFrame>2500||phase!=='live')throw new Error('No live video');
  captureBusy=true;$('photo').disabled=true;
  try{
    const frame=document.createElement('canvas');frame.width=video.videoWidth;frame.height=video.videoHeight;
    frame.getContext('2d').drawImage(video,0,0);
    const blob=await new Promise((resolve,reject)=>frame.toBlob(b=>b?resolve(b):reject(new Error('Could not capture photo')),'image/png'));
    const response=await fetch('/api/snapshot',{method:'POST',headers:{'Content-Type':'image/png','X-Photo-Token':photoToken,...headers},body:blob,signal:AbortSignal.timeout(10000)});
    const result=await response.json();
    if(!response.ok){const error=new Error(result.error||'Photo save failed');error.status=response.status;throw error;}
    log('Photo saved: '+result.name);return result;
  }catch(error){log('Photo save failed: '+error.message);throw error;}
  finally{captureBusy=false;}
}
window.captureFrame=captureFrame;
$('photo').onclick=async()=>{
  if(window.onPhotoButton)return window.onPhotoButton();   // the map page decides where the photo goes
  try{const result=await captureFrame();$('photoStatus').textContent=(result.desktop_saved?'Saved to Desktop and Photos: ':'Saved to Photos (Desktop copy unavailable): ')+result.name;}
  catch(error){$('photoStatus').textContent='Photo not saved: '+error.message;}
};
let linkWasUp=null,healthBusy=false;
async function checkLink(){
  if(!localSaver||healthBusy)return;healthBusy=true;
  try{
    const r=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(2000)});const h=await r.json();
    $('linkStatus').textContent=h.reachable?'Bridge path responding · '+h.latencyMs+' ms HTTP response':'Bridge path not responding · probing once per second';
    if(linkWasUp===false&&h.reachable&&enabled){log('Bridge path restored; reconnecting video now');if(retry)clearTimeout(retry);retry=null;connect();}
    if(linkWasUp!==h.reachable)log(h.reachable?'Bridge Ethernet path reachable':'Bridge Ethernet path unreachable');
    linkWasUp=h.reachable;
  }catch(e){$('linkStatus').textContent='Local photo helper unavailable';}finally{healthBusy=false;}
}
setInterval(checkLink,1000);checkLink();
for(const event of ['online','pageshow'])window.addEventListener(event,()=>{if(enabled&&phase!=='live'){if(retry)clearTimeout(retry);retry=null;connect();}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&enabled&&phase!=='live'){if(retry)clearTimeout(retry);retry=null;connect();}});

$('stop').onclick=()=>{enabled=false;epoch++;if(retry)clearTimeout(retry);retry=null;clearConnection();if(inOutage){outageMs+=performance.now()-outageStart;inOutage=false;}phase='stopped';lastFrame=0;for(const id of ['rate','fps','loss','age'])$(id).textContent='—';setStatus('Stopped','');$('overlay').classList.remove('hidden');$('message').textContent='Test stopped';$('detail').textContent='Press Start / reconnect to resume.';log('Test stopped');};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify({ethernetIP:ip,events,samples,interruptions:drops,interruptedSeconds:(outageMs+(inOutage?performance.now()-outageStart:0))/1000},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='bridge-test-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);};
setInterval(()=>{
  $('photo').disabled=captureBusy||!localSaver||phase!=='live'||video.readyState<2;
  $('downtime').textContent=((outageMs+(inOutage?performance.now()-outageStart:0))/1000).toFixed(1)+' s';
  if(inOutage){$('rate').textContent='0.00 Mb/s';$('fps').textContent='0';$('loss').textContent='—';$('age').textContent=((performance.now()-outageStart)/1000).toFixed(1)+' s';}
  timeline.push(phase);if(timeline.length>90)timeline.shift();const c=$('history'),r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.width=r.width*d;c.height=r.height*d;const x=c.getContext('2d');
  const colors={live:'#69c995',outage:'#f27d7d',connecting:'#dfb96b',stopped:'#536170',ready:'#536170'};const w=c.width/90;
  timeline.forEach((s,i)=>{x.fillStyle=colors[s];x.fillRect((90-timeline.length+i)*w,8*d,Math.max(1,w-d),c.height-16*d);});
},1000);
window.addEventListener('beforeunload',()=>{enabled=false;clearConnection();});
start();
