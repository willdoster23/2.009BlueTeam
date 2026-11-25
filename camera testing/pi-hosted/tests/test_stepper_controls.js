'use strict';
const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm'),test=require('node:test');
test('Y settings cap RPM at 200 and lower it for finer pulse scaling without changing X',()=>{
  const nodes=new Map(),events={};
  function node(id){
    if(!nodes.has(id))nodes.set(id,{value:'',checked:false,hidden:false,disabled:false,
      addEventListener(name,fn){this[name]=fn;},closest(){return {};},querySelectorAll(){return [];}});
    return nodes.get(id);
  }
  const roof={poweroff:{}};
  for(const axis of ['x','y'])roof[axis+'Axis']={drive:'stepper',motorConnected:true,motorArmed:false,
    maxMotorRPM:axis==='y'?200:187.5,maxPulsesPerSecond:axis==='y'?6000:5000,motorRPMCap:axis==='y'?200:null,
    stepperSettings:{pulsesPerRev:1600,rpm:3,accel:10,travelPerRev:3,positiveDirection:1,calibrated:true,alarmMode:0}};
  const context={window:{blueInspection:{state:()=>({roof:{data:roof}}),movementBusy:()=>false,inspectionActive:()=>false},
    addEventListener:(name,fn)=>events[name]=fn},document:{getElementById:node,addEventListener:()=>{}}};
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../app/stepper-controls.js'),'utf8'),context);
  events.inspectionposition();
  assert.equal(Number(node('yMotorRpm').max),200);
  assert.equal(Number(node('xMotorRpm').max),187.5);
  assert.match(node('yMotorSettingsStatus').textContent,/Y is capped at 200 RPM/);
  node('yMotorPpr').value=6400;node('yMotorPpr').input();
  assert.equal(Number(node('yMotorRpm').max),56.25);
  assert.equal(Number(node('xMotorRpm').max),187.5);
  node('yMotorPpr').value=400;node('yMotorPpr').input();
  assert.equal(Number(node('yMotorRpm').max),200);
});

function controlsHarness() {
  const nodes=new Map(),events={},calls=[];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id,{value:'',checked:false,hidden:false,disabled:false,
      reportValidity(){return true;},get valueAsNumber(){return Number(this.value);},
      addEventListener(name,fn){this[name]=fn;},closest(){return {};},querySelectorAll(){return [];}});
    return nodes.get(id);
  }
  const roof={poweroff:{}};
  for (const axis of ['x','y']) roof[axis+'Axis']={drive:'stepper',motorConnected:true,motorArmed:false,zeroed:true,
    encoderSignalValid:true,maxMotorRPM:axis==='y'?200:187.5,maxPulsesPerSecond:axis==='y'?6000:5000,motorRPMCap:axis==='y'?200:null,
    stepperSettings:{pulsesPerRev:1600,rpm:3,accel:10,travelPerRev:3,positiveDirection:1,calibrated:true,alarmMode:0}};
  const ui={state:()=>({roof:{data:roof}}),movementBusy:()=>false,inspectionActive:()=>false,
    async sendAxis(axis,payload){
      calls.push({axis,...payload});const state=roof[axis+'Axis'];
      if(payload.action==='stop')return {...state,motorArmed:false};
      if(payload.action==='arm')return {...state,motorArmed:true};
      if(payload.action==='stepper-settings')return {...state,stepperSettings:payload.settings};
      throw new Error('Unexpected motor action');
    }};
  const context={window:{blueInspection:ui,brickStorage:{id:()=>String(calls.length)},addEventListener:(name,fn)=>events[name]=fn},
    document:{getElementById:node,addEventListener:()=>{}}};
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../app/stepper-controls.js'),'utf8'),context);
  events.inspectionposition();
  return {node,roof,calls,readiness:context.window.blueStepperControls.readiness,paint:events.inspectionposition};
}
test('zero alone explains calibration and arming requirements for inch moves',()=>{
  const {roof,readiness}=controlsHarness(),state=roof.xAxis;
  state.stepperSettings.calibrated=false;
  assert.match(readiness('x',state),/distance scale in Setup/);
  state.stepperSettings.calibrated=true;state.zeroed=false;
  assert.match(readiness('x',state),/Set X zero/);
  state.zeroed=true;
  assert.match(readiness('x',state),/Press Arm X motor/);
  state.motorArmed=true;
  assert.match(readiness('x',state),/Ready to move in inches/);
  roof.yAxis.encoderSignalValid=false;
  assert.match(readiness('y',roof.yAxis),/valid pulley encoder/);
});
test('manual speed applies only RPM, disarms first, and preserves calibration and zero',async()=>{
  const {node,roof,calls,paint}=controlsHarness();roof.xAxis.motorArmed=true;paint();
  node('xMotorTravel').value='8';node('xMotorTravel').input();
  node('xMotorRpm').value='12';node('xMotorRpm').input();
  await node('xMotorSpeedSave').onclick();
  assert.deepEqual(calls.map(c=>c.action),['stop','stepper-settings']);
  assert.equal(calls[1].settings.rpm,12);
  assert.equal(calls[1].settings.travelPerRev,3,'unsaved advanced geometry must not leak into speed changes');
  assert.equal(calls[1].settings.calibrated,true);
  assert.equal(roof.xAxis.zeroed,true);assert.equal(roof.xAxis.motorArmed,false);
  assert.equal(Number(node('xMotorRpm').value),12);
});
test('Arm toggles to Disarm and back without a movement command',async()=>{
  const {node,roof,calls}=controlsHarness();
  await node('xMotorArm').onclick();
  assert.equal(roof.xAxis.motorArmed,true);assert.equal(node('xMotorArm').textContent,'Disarm X motor');
  assert.equal(node('xMotorArm').disabled,false);
  await node('xMotorArm').onclick();
  assert.equal(roof.xAxis.motorArmed,false);
  assert.deepEqual(calls.map(c=>c.action),['arm','stop']);
});

