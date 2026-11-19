
'use strict';
const $=id=>document.getElementById(id), video=$('video');
const photoToken='__PHOTO_TOKEN__';
const isPreview=document.body.dataset.preview==='true';
const correctedVideo=$('correctedVideo'),lensToggle=$('lensToggle');
let lensRenderer=null,lensEnabled=!isPreview,lensFrame=null;

function makeLensRenderer(canvas){
  const gl=canvas.getContext('webgl',{alpha:false,antialias:false});
  if(!gl)return null;
  const program=gl.createProgram();
  function compile(type,code){
    const shader=gl.createShader(type);
    gl.shaderSource(shader,code);
    gl.compileShader(shader);
    if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));
    gl.attachShader(program,shader);
    gl.deleteShader(shader);
  }
  compile(gl.VERTEX_SHADER,`
    attribute vec2 vertex;
    void main() { gl_Position = vec4(vertex, 0.0, 1.0); }
  `);
  compile(gl.FRAGMENT_SHADER,`
    precision highp float;
    uniform sampler2D image;
    uniform vec2 size, sourceCenter;
    uniform float sourceFocal, outputFocal;
    void main() {
      vec2 pixel = vec2(gl_FragCoord.x, size.y - gl_FragCoord.y) - 0.5;
      vec2 ray = (pixel - (size - 1.0) * 0.5) / outputFocal;
      float radius = length(ray);
      float factor = radius > 0.00001 ? atan(radius) / radius : 1.0;
      vec2 perspective = sourceCenter + ray * (sourceFocal * factor);
      vec2 source = mix(pixel, perspective, 0.92);
      if (any(lessThan(source, vec2(0.0))) || any(greaterThan(source, size - 1.0))) {
        gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
      } else {
        gl_FragColor = texture2D(image, (source + 0.5) / size);
      }
    }
  `);
  gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  const buffer=gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
  gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
  const vertex=gl.getAttribLocation(program,'vertex');
  gl.enableVertexAttribArray(vertex);
  gl.vertexAttribPointer(vertex,2,gl.FLOAT,false,0,0);
  const texture=gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D,texture);
  for(const edge of [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T])gl.texParameteri(gl.TEXTURE_2D,edge,gl.CLAMP_TO_EDGE);
  for(const filter of [gl.TEXTURE_MIN_FILTER,gl.TEXTURE_MAG_FILTER])gl.texParameteri(gl.TEXTURE_2D,filter,gl.LINEAR);
  const size=gl.getUniformLocation(program,'size');
  const center=gl.getUniformLocation(program,'sourceCenter');
  const sourceFocal=gl.getUniformLocation(program,'sourceFocal');
  const outputFocal=gl.getUniformLocation(program,'outputFocal');
  let previousWidth=0,previousHeight=0;
  return (source,width,height)=>{
    if(gl.isContextLost())return false;
    if(width!==previousWidth||height!==previousHeight){
      canvas.width=previousWidth=width;
      canvas.height=previousHeight=height;
      gl.viewport(0,0,width,height);
      // Scale the earlier 1493 × 1119 equidistant fit to the stream width.
      // Keep the fitted center offset relative to the center of the 16:9 crop.
      const scale=width/1493;
      gl.uniform2f(size,width,height);
      gl.uniform2f(center,(width-1)/2+3.725800*scale,(height-1)/2+16.345500*scale);
      gl.uniform1f(sourceFocal,530.144924*scale);
      gl.uniform1f(outputFocal,width/(2*Math.tan(125*Math.PI/360)));
    }
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,source);
    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    return true;
  };
}

function showCorrection(show){
  correctedVideo.hidden=!show;
  video.classList.toggle('lens-source',show);
}

function updateLensFrame(){
  if(!lensEnabled||!lensRenderer||video.readyState<2||!video.videoWidth)return false;
  try{
    const rendered=lensRenderer(video,video.videoWidth,video.videoHeight);
    showCorrection(rendered);
    return rendered;
  }catch(error){
    lensRenderer=null;
    lensToggle.disabled=true;
    lensToggle.title='Lens correction is unavailable in this browser.';
    showCorrection(false);
    log('Lens correction unavailable: '+error.message);
    return false;
  }
}

function lensLoop(){
  lensFrame=null;
  updateLensFrame();
  if(!video.paused)scheduleLensFrame();
}

function scheduleLensFrame(){
  if(lensFrame!==null||!lensEnabled||!lensRenderer)return;
  lensFrame=video.requestVideoFrameCallback?video.requestVideoFrameCallback(lensLoop):requestAnimationFrame(lensLoop);
}

if(!isPreview){
  try{lensRenderer=makeLensRenderer(correctedVideo);}catch{lensRenderer=null;}
  $('lensOption').hidden=!lensRenderer;
  lensToggle.onchange=()=>{
    lensEnabled=lensToggle.checked;
    if(lensEnabled){updateLensFrame();scheduleLensFrame();}
    else showCorrection(false);
  };
  video.addEventListener('playing',scheduleLensFrame);
  correctedVideo.addEventListener('webglcontextlost',event=>{
    event.preventDefault();
    showCorrection(false);
  });
  correctedVideo.addEventListener('webglcontextrestored',()=>{
    try{lensRenderer=makeLensRenderer(correctedVideo);}catch{lensRenderer=null;}
    scheduleLensFrame();
  });
}
let previewFrame=null;
$('photoStatus').textContent='';
let pc=null,session=null,abort=null,retry=null,watch=null,epoch=0,enabled=false,phase='ready';
let started=0,lastFrame=0,previous=null,everLive=false,inOutage=false,outageStart=0,outageMs=0,drops=0;
let samples=[],events=[],timeline=[],ip='192.168.2.3';
const linkName=()=>'Camera Ethernet';
function log(message){events.push({time:new Date().toISOString(),message});if(events.length>1000)events.shift();$('events').textContent=events.slice(-12).map(x=>new Date(x.time).toLocaleTimeString()+'  '+x.message).reverse().join('\n');}
function setStatus(text,kind){$('status').textContent=text;$('status').className='status '+kind;}
function interrupted(){if(everLive&&!inOutage){inOutage=true;outageStart=lastFrame||performance.now();drops++;$('drops').textContent=drops;log('Video interrupted');}}
function clearConnection(){
  if(watch)clearInterval(watch);watch=null;
  if(abort)abort.abort();abort=null;
  if(pc){pc.close();pc=null;}
  if(session){fetch(session,{method:'DELETE',headers:{'X-Photo-Token':photoToken},signal:AbortSignal.timeout(2000)}).catch(()=>{});session=null;}
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
  const endpoint=new URL('/media/video/whep',location.href);
  localPc.addTransceiver('video',{direction:'recvonly'});
  localPc.ontrack=ev=>{if(token!==epoch)return;video.srcObject=ev.streams[0]||new MediaStream([ev.track]);video.play().catch(()=>{$('resume').hidden=false;log('Playback needs a click: use Resume video.');});};
  localPc.onconnectionstatechange=()=>{if(token===epoch&&localPc.connectionState==='failed')schedule('WebRTC connection failed');};
  watch=setInterval(()=>sample(token,localPc),1000);
  const timeout=setTimeout(()=>controller.abort(),3500);
  try{
    await localPc.setLocalDescription(await localPc.createOffer());
    if(localPc.iceGatheringState!=='complete')await new Promise(resolve=>{const t=setTimeout(resolve,2000);localPc.addEventListener('icegatheringstatechange',()=>{if(localPc.iceGatheringState==='complete'){clearTimeout(t);resolve();}});});
    if(token!==epoch)return;
    const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/sdp','X-Photo-Token':photoToken},body:localPc.localDescription.sdp,signal:controller.signal});
    if(!response.ok)throw new Error('Stream response HTTP '+response.status);
    const location=response.headers.get('Location');const newSession=location?new URL(location,endpoint).href:null;
    if(token!==epoch){if(newSession)fetch(newSession,{method:'DELETE',headers:{'X-Photo-Token':photoToken},signal:AbortSignal.timeout(2000)}).catch(()=>{});return;}
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
      $('route').textContent=remoteIP?'Media route · '+(localIP||'Mac')+' → '+remoteIP:'Media endpoint · '+ip+' (browser hides candidate addresses)';
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
      everLive=true;phase='live';setStatus('LIVE · '+linkName(),'live');$('overlay').classList.add('hidden');
    }else if(lastFrame&&age>2){
      interrupted();phase='outage';setStatus('VIDEO FROZEN','bad');$('overlay').classList.remove('hidden');$('message').textContent='Video frozen';$('detail').textContent='No new decoded frames · automatic retry pending';
    }
    samples.push({time:new Date().toISOString(),state:phase,mbps:+mbps.toFixed(3),fps:+fps.toFixed(1),packetLossPercent:loss,lastFrameAgeSec:age});if(samples.length>14400)samples.shift();
    if((lastFrame&&age>2.5)||(!lastFrame&&now-started>6500))schedule('No decoded video frames');
  }catch(error){if(token===epoch)schedule('Video statistics unavailable: '+error.message);}
}
function start(){
  enabled=false;epoch++;if(retry)clearTimeout(retry);retry=null;clearConnection();
  if(inOutage){outageMs+=performance.now()-outageStart;inOutage=false;}
  ip='192.168.2.3';enabled=true;$('route').textContent=linkName()+' · '+ip;log('Connecting to camera');connect();
}
$('start').onclick=start;
$('resume').onclick=async()=>{try{await video.play();$('resume').hidden=true;}catch(e){log('Playback: '+e.message);}};
video.addEventListener('playing',()=>{$('resume').hidden=true;});
let captureBusy=false;
function cameraReady(){
  if(cameraSwitching||window.blueInspection?.state()?.robot.data?.camera?.switching)return false;
  if(isPreview)return !!previewFrame;
  return !video.paused && video.readyState>=2 && lastFrame && performance.now()-lastFrame<=2500 && phase==='live';
}
async function captureFrame(){
  if(!cameraReady())throw new Error('Wait for live video before taking a photo.');
  const frame=previewFrame||document.createElement('canvas');
  const evidence=previewFrame?drawPreview():{};
  if(!previewFrame){
    frame.width=video.videoWidth;frame.height=video.videoHeight;
    frame.getContext('2d').drawImage(updateLensFrame()?correctedVideo:video,0,0);
  }
  const blob=await new Promise((resolve,reject)=>frame.toBlob(b=>b?resolve(b):reject(new Error('Could not capture photo')),'image/png'));
  return {blob,width:frame.width,height:frame.height,...evidence};
}
window.brickCamera={ready:cameraReady,capture:captureFrame,busy:()=>captureBusy||cameraSwitching,previewIdentity};
$('photo').onclick=async()=>{
  if(captureBusy)return;
  let position=null;
  if($('encoderX')){
    if(!window.blueInspection?.positionMeasured()){
      $('photoStatus').textContent='Wait for a fresh X and Y readings from the controller.';return;
    }
    if(window.blueInspection.movementBusy()){
      $('photoStatus').textContent='Wait for movement to finish before capturing.';return;
    }
    position={...window.blueInspection.state().roof.data.position,status:$('brickStatus').value};
  }

  if(!isPreview&&video.paused){try{await video.play();}catch(e){$('photoStatus').textContent='Press Play on the video, then take a photo.';return;}}
  if(pc)await sample(epoch,pc);
  if(!cameraReady()){$('photoStatus').textContent='Wait for live video before taking a photo.';return;}
  captureBusy=true;$('photo').disabled=true;
  try{
    const {blob,width,height}=await captureFrame();
    const metadata=position?window.brickStorage.positionMetadata(position):{};
    if(isPreview)metadata.simulated=true;
    const result=await window.brickStorage.save(blob,width,height,position,metadata);
    if (result.record) {
      $('photoStatus').textContent=metadata.x_inches===undefined
        ? `Saved to map at X ${position.x}, Y ${position.y} counts.`
        : `Saved to map at X ${metadata.x_inches} in, Y ${metadata.y_inches} in.`;
      window.dispatchEvent(new Event('brickcaptured'));
    } else {
      window.brickStorage.download(blob,result.name);
      $('photoStatus').textContent='Photo download requested.';
    }
    log('Photo captured: '+result.name);
  }catch(error){$('photoStatus').textContent='Photo not saved: '+error.message;log('Photo save failed: '+error.message);}
  finally{captureBusy=false;}
};
let linkWasUp=null,healthBusy=false;
async function checkLink(){
  if(healthBusy)return;healthBusy=true;
  try{
    const r=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(2000)});const h=await r.json();
    const link=linkName();
    $('linkStatus').textContent=$('roofState')
      ? (h.reachable?'Camera connection available':'Camera unavailable · reconnecting automatically')
      : (h.reachable?link+' responding · '+h.latencyMs+' ms HTTP response':link+' not responding · probing once per second');
    if(linkWasUp===false&&h.reachable&&enabled){log(link+' restored; reconnecting video now');if(retry)clearTimeout(retry);retry=null;connect();}
    if(linkWasUp!==h.reachable)log(link+(h.reachable?' reachable':' unreachable'));
    linkWasUp=h.reachable;
  }catch(e){$('linkStatus').textContent='Rooftop mapper unavailable';}finally{healthBusy=false;}
}
if(!isPreview){setInterval(checkLink,1000);checkLink();}
for(const event of ['online','pageshow'])window.addEventListener(event,()=>{if(enabled&&phase!=='live'){if(retry)clearTimeout(retry);retry=null;connect();}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&enabled&&phase!=='live'){if(retry)clearTimeout(retry);retry=null;connect();}});

$('stop').onclick=()=>{enabled=false;epoch++;if(retry)clearTimeout(retry);retry=null;clearConnection();if(inOutage){outageMs+=performance.now()-outageStart;inOutage=false;}phase='stopped';lastFrame=0;for(const id of ['rate','fps','loss','age'])$(id).textContent='—';setStatus('Stopped','');$('overlay').classList.remove('hidden');$('message').textContent='Test stopped';$('detail').textContent='Press Start / reconnect to resume.';log('Test stopped');};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify({ethernetIP:ip,events,samples,interruptions:drops,interruptedSeconds:(outageMs+(inOutage?performance.now()-outageStart:0))/1000},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='bridge-test-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);};
setInterval(()=>{
  $('photo').disabled=captureBusy||!cameraReady()||!!($('encoderX')&&
    (!window.blueInspection?.positionMeasured()||window.blueInspection.movementBusy()));
  $('downtime').textContent=((outageMs+(inOutage?performance.now()-outageStart:0))/1000).toFixed(1)+' s';
  if(inOutage){$('rate').textContent='0.00 Mb/s';$('fps').textContent='0';$('loss').textContent='—';$('age').textContent=((performance.now()-outageStart)/1000).toFixed(1)+' s';}
  timeline.push(phase);if(timeline.length>90)timeline.shift();const c=$('history'),r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.width=r.width*d;c.height=r.height*d;const x=c.getContext('2d');
  const colors={live:'#69c995',outage:'#f27d7d',connecting:'#dfb96b',stopped:'#536170',ready:'#536170'};const w=c.width/90;
  timeline.forEach((s,i)=>{x.fillStyle=colors[s];x.fillRect((90-timeline.length+i)*w,8*d,Math.max(1,w-d),c.height-16*d);});
},1000);
window.addEventListener('beforeunload',()=>{enabled=false;clearConnection();});
const previewBrickWidth=203,previewBrickHeight=79,previewPixelsPerInch=29;
function previewGeometry(position, slip=0){
  const dx=(position?.xInches||0)*previewPixelsPerInch;
  const dy=((position?.yInches||0)+slip)*previewPixelsPerInch;
  const row=Math.floor((dy+previewBrickHeight*3.5)/previewBrickHeight);
  const column=Math.floor((dx+previewBrickWidth*2.25-(row%2?previewBrickWidth/2:0))/previewBrickWidth);
  return {dx,dy,left:column*previewBrickWidth+(row%2?previewBrickWidth/2:0)-dx,
    top:row*previewBrickHeight-dy,brick:{source:'preview',course:row-2,column:column+1}};
}

function previewIdentity(position){
  return previewGeometry(position).brick;
}

function drawPreview(){
  const width=960,height=width*previewFrame.height/previewFrame.width;
  const ctx=previewFrame.getContext('2d');
  const scale=previewFrame.width/width;
  ctx.setTransform(scale,0,0,scale,0,0);
  const position=window.blueInspection?.state()?.roof.data.position;
  const slip=$('previewSlip').valueAsNumber||0;
  const {dx,dy,left,top,brick}=previewGeometry(position,slip);
  const brickWidth=previewBrickWidth,brickHeight=previewBrickHeight;
  const palette=['#a75f48','#b67358','#95513d','#ac6750','#b97051','#9a573e'];
  ctx.fillStyle='#b8a798';ctx.fillRect(0,0,width,height);
  const firstRow=Math.floor(dy/brickHeight);
  for(let row=firstRow;row<=firstRow+Math.ceil(height/brickHeight);row++){
    const stagger=row%2?brickWidth/2:0;
    const firstCol=Math.floor((dx-stagger)/brickWidth);
    for(let col=firstCol;col<=firstCol+1+Math.ceil(width/brickWidth);col++){
      const x=col*brickWidth+stagger-dx,y=row*brickHeight-dy;
      const tint=Math.abs(row*19+col*23)%palette.length;
      ctx.fillStyle='#785548';ctx.fillRect(x+4,y+4,brickWidth-6,brickHeight-6);
      ctx.fillStyle=palette[tint];ctx.fillRect(x+3,y+3,brickWidth-9,brickHeight-9);
      ctx.fillStyle='#fff1dc1b';ctx.fillRect(x+6,y+6,brickWidth-16,3);
      ctx.fillStyle='#502a211a';
      for(let dot=0;dot<15;dot++){
        const u=Math.abs(row*17+col*11+dot*43)%174;
        const v=Math.abs(row*13+col*31+dot*23)%58;
        ctx.fillRect(x+10+u,y+10+v,4+dot%5,2);
      }
    }
  }
  const shade=ctx.createRadialGradient(width/2,height/2,70,width/2,height/2,width/2+80);
  shade.addColorStop(0,'#0000');shade.addColorStop(1,'#24191169');
  ctx.fillStyle=shade;ctx.fillRect(0,0,width,height);
  const tapX=brickWidth*($('brickTapZone').value==='edge'?2.1:2.25),tapY=brickHeight*3.5;
  const metal=ctx.createLinearGradient(tapX-8,tapY,tapX+8,tapY);
  metal.addColorStop(0,'#65401c');metal.addColorStop(.5,'#e5b878');metal.addColorStop(1,'#9d602e');
  ctx.fillStyle=metal;ctx.fillRect(tapX-8,tapY+5,16,34);
  ctx.fillStyle='#c49151';ctx.fillRect(tapX-12,tapY+2,24,8);
  return {brick,tapPoint:[tapX/width,tapY/height],
    brickCrop:[(left+4)/width,(top+4)/height,(brickWidth-11)/width,(brickHeight-11)/height],
    nearJoint:Math.min(tapX-left,left+brickWidth-tapX,tapY-top,top+brickHeight-tapY)<10};
}

const cameraMode=$('cameraMode');
let appliedMode='1280x720', modesKey='', cameraSwitching=false, pendingMode=null, switchTimeout;

function rawView(){
  lensToggle.checked=false;
  lensEnabled=false;
  showCorrection(false);
}

function applyCameraMode(mode){
  if(appliedMode===mode)return;
  appliedMode=mode;
  rawView();
  if(isPreview){
    const [width,height]=mode.split('x').map(Number);
    previewFrame.width=width;previewFrame.height=height;
    previewFrame.parentElement.style.aspectRatio=`${width} / ${height}`;
    drawPreview();
  }else if(enabled)start();
}

function syncCameraMode(){
  const ui=window.blueInspection;
  const camera=ui?.state()?.robot.data?.camera;
  if(camera?.modes?.length){
    $('cameraModeTools').hidden=false;
    const key=camera.modes.join(',');
    if(key!==modesKey){
      modesKey=key;
      cameraMode.replaceChildren(...camera.modes.map(mode=>new Option(mode.replace('x',' × '),mode)));
    }
    applyCameraMode(camera.active);
    if(pendingMode&&camera.id===pendingMode){
      clearTimeout(switchTimeout);
      pendingMode=null;cameraSwitching=false;
      $('cameraModeStatus').textContent=camera.error||'Showing raw camera mode '+camera.active.replace('x',' × ')+'.';
    }
    if(!cameraSwitching)cameraMode.value=camera.active;
  }
  cameraMode.disabled=!camera?.modes?.length||camera.switching||cameraSwitching||captureBusy||ui?.inspectionActive()||ui?.movementBusy();
}

cameraMode.onchange=async()=>{
  if(cameraMode.disabled)return;
  const mode=cameraMode.value;
  cameraSwitching=true;
  syncCameraMode();
  rawView();
  const feedback=$('cameraModeStatus');
  try{
    feedback.textContent='Switching camera mode…';
    const response=await fetch('/api/camera',{method:'POST',headers:{'Content-Type':'application/json','X-Photo-Token':photoToken},
      body:JSON.stringify({mode}),signal:AbortSignal.timeout(3500)});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error);
    if(result.id){
      pendingMode=result.id;
      switchTimeout=setTimeout(()=>{
        pendingMode=null;cameraSwitching=false;
        feedback.textContent='Waiting for the camera to reconnect.';
        syncCameraMode();
      },15000);
    }else{
      applyCameraMode(result.active);
      feedback.textContent='Raw camera mode '+result.active.replace('x',' × ')+'.';
    }
  }catch(error){feedback.textContent=error.message;}
  finally{
    cameraSwitching=!!pendingMode;
    syncCameraMode();
  }
};
window.addEventListener('inspectionposition',syncCameraMode);
video.addEventListener('resize',()=>{
  if(video.videoWidth&&video.videoHeight)video.parentElement.style.aspectRatio=`${video.videoWidth} / ${video.videoHeight}`;
});

if(isPreview){
  previewFrame=document.createElement('canvas');
  previewFrame.width=1280;previewFrame.height=720;
  previewFrame.className='demo-camera';
  previewFrame.setAttribute('aria-label','Simulated masonry camera');
  video.hidden=true;video.before(previewFrame);
  $('overlay').classList.add('hidden');
  $('start').hidden=$('audioToggle').hidden=$('connectionDiagnostics').hidden=$('audioStatus').hidden=true;
  $('roofState').parentElement.classList.add('hidden');
  $('inspectionMode').querySelector('option[value="hardware"]').disabled=true;
  $('lensOption').hidden=false;
  $('previewSlipLabel').hidden=false;
  $('linkStatus').textContent='Sample camera · local preview';
  setStatus('Offline preview','live');
  drawPreview();
  setInterval(drawPreview,250);
}else start();
