import React, { useState, useEffect, useRef } from 'react';
import { useSwarmStore } from '../store/useSwarmStore';
import type { UserRole } from '../store/useSwarmStore';
import { DigitalTwinSimulator } from './DigitalTwinSimulator';
import { AnalyticsDashboard } from './AnalyticsDashboard';
import { 
  Play, 
  Crosshair, 
  Layers, 
  Database,
  RefreshCw,
  X
} from 'lucide-react';

interface MissionControlProps {
  onBackToLanding: () => void;
}

export const MissionControl: React.FC<MissionControlProps> = ({ onBackToLanding }) => {
  const {
    activeTab,
    setActiveTab,
    role,
    setRole,
    drones,
    tasks,
    obstacles,
    boundaries,
    setBoundaries,
    baseStation,
    missionType,
    setMissionType,
    missionStatus,
    swarmHealthScore,
    edgeNetwork,
    toggleEdgeNetwork,
    eventLogs,
    clearLogs,
    startBidding,
    launchSwarm,
    abortAndRTL,
    holdSwarm,
    killSwarm,
    resetPlatform,
    simulationStep,
    addObstacle
  } = useSwarmStore();

  const [mapStyle, setMapStyle] = useState<'GRID' | 'SATELLITE' | 'TERRAIN'>('GRID');
  const [drawingMode, setDrawingMode] = useState<boolean>(false);
  const [obstaclePlacementMode, setObstaclePlacementMode] = useState<boolean>(false);
  const mapContainerRef = useRef<SVGSVGElement>(null);

  // 100ms Telemetry Simulation Timer Loop
  useEffect(() => {
    const timer = setInterval(() => {
      simulationStep();
    }, 100);
    return () => clearInterval(timer);
  }, [simulationStep]);

  // Click handler to draw polygon (AOI) or place obstacle
  const handleMapClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = mapContainerRef.current?.getBoundingClientRect();
    if (!rect) return;

    // Calculate click coordinates relative to SVGSVGElement scale (800 x 500)
    const scaleX = 800 / rect.width;
    const scaleY = 500 / rect.height;
    const clickX = (e.clientX - rect.left) * scaleX;
    const clickY = (e.clientY - rect.top) * scaleY;

    if (obstaclePlacementMode) {
      addObstacle({
        id: `obs-${Date.now()}`,
        x: clickX,
        y: clickY,
        radius: 20,
        type: 'CRANE',
      });
      setObstaclePlacementMode(false);
      return;
    }

    if (drawingMode) {
      if (missionStatus !== 'IDLE' && missionStatus !== 'COMPLETED') return;
      const updatedBoundaries = [...boundaries, { x: clickX, y: clickY }];
      setBoundaries(updatedBoundaries);
    }
  };

  // Close polygon automatically
  const handleClosePolygon = () => {
    setDrawingMode(false);
  };

  // Clear drawn area
  const handleClearArea = () => {
    setBoundaries([]);
    setDrawingMode(false);
  };

  // Role permissions checks
  const canModifyControls = role === 'COMMANDER' || role === 'OPERATOR';
  const canTriggerOverride = role === 'COMMANDER';

  return (
    <div className="relative min-h-screen bg-cyber-bg text-slate-200 flex flex-col font-sans overflow-hidden select-none">
      
      {/* SCANLINE EFFECTS */}
      <div className="absolute inset-0 scanlines opacity-5 pointer-events-none z-50"></div>

      {/* TOP HEADER BAR */}
      <header className="relative z-20 border-b border-cyber-panelBorder/30 bg-cyber-darkGray/90 backdrop-blur-md px-4 py-2 flex items-center justify-between">
        
        {/* Logo and Back CTA */}
        <div className="flex items-center gap-3">
          <button 
            onClick={onBackToLanding}
            className="text-xs font-mono border border-cyber-blue/30 px-2.5 py-1 rounded bg-cyber-blue/5 hover:bg-cyber-blue hover:text-black uppercase transition-all"
          >
            ← Exit Console
          </button>
          
          <div className="hidden sm:block">
            <h1 className="text-sm font-black text-white tracking-wider">DRONE MATRX v2.4</h1>
            <p className="text-[8px] font-mono text-cyber-blue tracking-widest uppercase -mt-0.5">Tactical Command Center</p>
          </div>
        </div>

        {/* Master Mission Control Overrides */}
        <div className="flex items-center gap-2">
          <div className="text-[10px] font-mono text-slate-500 uppercase mr-1 hidden md:block">Swarm Overrides:</div>
          
          <button
            disabled={!canModifyControls || missionStatus === 'IDLE' || missionStatus === 'COMPLETED'}
            onClick={holdSwarm}
            className="border border-cyber-orange/40 bg-cyber-orange/10 hover:bg-cyber-orange/30 disabled:opacity-40 disabled:pointer-events-none px-3 py-1.5 rounded text-xs font-mono text-cyber-orange font-bold uppercase transition-all flex items-center gap-1.5"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-cyber-orange animate-pulse"></span>
            Hold Swarm
          </button>

          <button
            disabled={!canModifyControls || missionStatus === 'IDLE' || missionStatus === 'COMPLETED'}
            onClick={abortAndRTL}
            className="border border-cyber-red/50 bg-cyber-red/10 hover:bg-cyber-red/35 disabled:opacity-40 disabled:pointer-events-none px-3 py-1.5 rounded text-xs font-mono text-cyber-red font-bold uppercase transition-all flex items-center gap-1.5 shadow-[0_0_8px_rgba(255,7,58,0.15)]"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-cyber-red animate-ping"></span>
            RTL Swarm
          </button>

          {canTriggerOverride && (
            <button
              onClick={killSwarm}
              className="border border-red-600 bg-red-600/20 hover:bg-red-600/40 px-3 py-1.5 rounded text-xs font-mono text-red-400 font-black uppercase transition-all"
            >
              ☢ TERMINATE
            </button>
          )}
        </div>

        {/* Edge Connections & Role Selector */}
        <div className="flex items-center gap-3">
          
          {/* Network Link Toggles */}
          <div className="flex items-center gap-2 border border-slate-800 bg-cyber-darkGray/50 px-2 py-1 rounded text-[9px] font-mono select-none">
            <span className="text-slate-500 mr-1 uppercase">Edge Links:</span>
            
            <button 
              onClick={() => toggleEdgeNetwork('wifiMeshConnected')}
              className={`flex items-center gap-0.5 ${edgeNetwork.wifiMeshConnected ? 'text-cyber-green' : 'text-slate-600 hover:text-slate-400'}`}
              title="WiFi Mesh Link"
            >
              WiFi
            </button>
            
            <button 
              onClick={() => toggleEdgeNetwork('lteConnected')}
              className={`flex items-center gap-0.5 ${edgeNetwork.lteConnected ? 'text-cyber-green' : 'text-slate-600 hover:text-slate-400'}`}
              title="LTE Backup Link"
            >
              LTE
            </button>

            <span className={`px-1.5 rounded-sm ${edgeNetwork.loraFallbackActive ? 'bg-cyber-orange/10 border border-cyber-orange/30 text-cyber-orange' : 'text-slate-600'}`} title="LoRa Link status">
              LORA
            </span>

            <span className="text-slate-500 font-bold border-l border-slate-800 pl-1.5">
              {edgeNetwork.latencyMs > 0 ? `${edgeNetwork.latencyMs}ms` : 'OFFLINE'}
            </span>
          </div>

          {/* User Role selector */}
          <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 px-2 py-1 rounded">
            <span className="text-[9px] font-mono text-slate-500 uppercase mr-1">Role:</span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className="bg-transparent text-white text-xs font-mono font-bold focus:outline-none cursor-pointer"
            >
              <option value="COMMANDER" className="bg-slate-950 text-white">Commander</option>
              <option value="OPERATOR" className="bg-slate-950 text-white">Operator</option>
              <option value="OBSERVER" className="bg-slate-950 text-white">Observer</option>
            </select>
          </div>

        </div>

      </header>

      {/* MAIN CONTAINER GRID */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-12 overflow-hidden">
        
        {/* LEFT CONTROL PANEL (Operations, SITL, Analytics) */}
        <aside className="lg:col-span-3 border-r border-cyber-panelBorder/20 bg-cyber-darkGray/40 backdrop-blur-md flex flex-col overflow-y-auto">
          
          {/* Sub-panel Tabs Navigation */}
          <nav className="grid grid-cols-4 border-b border-slate-800 text-[10px] font-mono text-center">
            <button
              onClick={() => setActiveTab('OPERATIONS')}
              className={`py-3 uppercase border-r border-slate-800 font-bold transition-all ${
                activeTab === 'OPERATIONS' ? 'text-cyber-blue bg-cyber-blue/5 border-b-2 border-b-cyber-blue' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Operations
            </button>
            <button
              onClick={() => setActiveTab('DIGITAL_TWIN')}
              className={`py-3 uppercase border-r border-slate-800 font-bold transition-all ${
                activeTab === 'DIGITAL_TWIN' ? 'text-cyber-blue bg-cyber-blue/5 border-b-2 border-b-cyber-blue' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              SITL Sim
            </button>
            <button
              onClick={() => setActiveTab('ANALYTICS')}
              className={`py-3 uppercase border-r border-slate-800 font-bold transition-all ${
                activeTab === 'ANALYTICS' ? 'text-cyber-blue bg-cyber-blue/5 border-b-2 border-b-cyber-blue' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Analytics
            </button>
            <button
              onClick={() => setActiveTab('SETTINGS')}
              className={`py-3 uppercase font-bold transition-all ${
                activeTab === 'SETTINGS' ? 'text-cyber-blue bg-cyber-blue/5 border-b-2 border-b-cyber-blue' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Edge Sys
            </button>
          </nav>

          {/* Tab Contents */}
          <div className="p-4 flex-1">
            {activeTab === 'OPERATIONS' && (
              <div className="space-y-5">
                
                {/* Mission selection panel */}
                <div className="glass-panel p-4 rounded corner-bracket relative">
                  <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-3 flex items-center gap-1.5">
                    <Layers size={14} /> 1. Objective Selector
                  </h3>

                  <div className="space-y-2">
                    {[
                      { type: 'SAR', label: 'SAR Grid Search', desc: 'Divide areas for thermal camera search sweeps' },
                      { type: 'AGRI', label: 'Crop Chemical Spray', desc: 'Allocate pesticide spraying lawnmower grids' },
                      { type: 'INSPECT', label: 'Infrastructure Check', desc: 'Scan steel structures or wind turbine vectors' },
                      { type: 'PATROL', label: 'Perimeter Patrol', desc: 'Monitor borders on decentralized mesh links' }
                    ].map(opt => (
                      <button
                        key={opt.type}
                        disabled={missionStatus !== 'IDLE' && missionStatus !== 'COMPLETED'}
                        onClick={() => setMissionType(opt.type as any)}
                        className={`w-full text-left p-2.5 rounded border transition-all ${
                          missionType === opt.type 
                            ? 'border-cyber-blue bg-cyber-blue/10 text-white shadow-cyber-blue' 
                            : 'border-slate-800 bg-cyber-darkGray/30 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                        }`}
                      >
                        <div className="text-xs font-bold font-sans tracking-wide uppercase">{opt.label}</div>
                        <div className="text-[10px] font-mono mt-0.5 opacity-80">{opt.desc}</div>
                      </button>
                    ))}
                  </div>
                  <div className="corner-bracket-child"></div>
                </div>

                {/* Workflow planning */}
                <div className="glass-panel p-4 rounded corner-bracket relative">
                  <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-3 flex items-center gap-1.5">
                    <Crosshair size={14} /> 2. Boundary Settings
                  </h3>

                  <p className="text-[11px] text-slate-400 leading-relaxed mb-4">
                    Toggle Draw Area mode, then click on the map to define the polygon boundary coordinates.
                  </p>

                  <div className="flex flex-col gap-2">
                    <button
                      disabled={missionStatus !== 'IDLE' && missionStatus !== 'COMPLETED'}
                      onClick={() => {
                        setDrawingMode(!drawingMode);
                        setObstaclePlacementMode(false);
                      }}
                      className={`w-full py-2.5 rounded text-xs font-mono uppercase tracking-wider transition-all border ${
                        drawingMode 
                          ? 'bg-cyber-blue border-cyber-blue text-black font-bold' 
                          : 'border-cyber-blue/30 text-cyber-blue hover:bg-cyber-blue/5'
                      }`}
                    >
                      {drawingMode ? 'Drawing Active...' : 'Draw Area (AOI)'}
                    </button>

                    <div className="flex gap-2">
                      <button
                        disabled={boundaries.length < 3 || (missionStatus !== 'IDLE' && missionStatus !== 'COMPLETED')}
                        onClick={handleClosePolygon}
                        className="flex-1 py-1.5 border border-slate-700 text-slate-300 hover:border-slate-500 rounded text-[10px] font-mono uppercase transition-all disabled:opacity-40"
                      >
                        Lock Area
                      </button>
                      <button
                        disabled={boundaries.length === 0}
                        onClick={handleClearArea}
                        className="flex-1 py-1.5 border border-cyber-red/30 text-cyber-red hover:border-cyber-red rounded text-[10px] font-mono uppercase transition-all"
                      >
                        Clear
                      </button>
                    </div>
                  </div>
                  
                  {boundaries.length > 0 && (
                    <div className="mt-3 font-mono text-[9px] text-slate-500">
                      BOUND VERTICES: {boundaries.length} locked<br />
                      AOI STATUS: {boundaries.length >= 3 ? 'VALID POLYGON' : 'NEED 3+ POINTS'}
                    </div>
                  )}
                  <div className="corner-bracket-child"></div>
                </div>

                {/* CBBA Solver triggering */}
                <div className="glass-panel p-4 rounded border-cyber-blue/20 bg-cyber-darkGray/60 relative">
                  <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-2">3. Deploy Swarm</h3>
                  
                  <p className="text-[11px] text-slate-400 mb-4">
                    Deploy Consensus task bidding (CBBA). Our decentralized edge core automatically partitions and uploads mission profiles.
                  </p>

                  <div className="flex flex-col gap-2">
                    <button
                      disabled={!canModifyControls || boundaries.length < 3 || missionType === 'NONE' || (missionStatus !== 'IDLE' && missionStatus !== 'COMPLETED')}
                      onClick={startBidding}
                      className="w-full py-2.5 bg-cyber-blue/15 border border-cyber-blue text-cyber-blue font-mono text-xs uppercase tracking-widest hover:bg-cyber-blue hover:text-black font-black transition-all rounded disabled:opacity-40 disabled:pointer-events-none"
                    >
                      {missionStatus === 'BIDDING' ? 'Solving CBBA...' : 'Bid & Plan Swarm'}
                    </button>

                    <button
                      disabled={!canModifyControls || missionStatus !== 'LAUNCHING'}
                      onClick={launchSwarm}
                      className="w-full py-3 bg-cyber-green text-black font-sans font-bold text-sm uppercase tracking-widest hover:bg-white transition-all rounded shadow-cyber-green disabled:opacity-40 disabled:pointer-events-none flex items-center justify-center gap-1.5"
                    >
                      <Play size={16} fill="black" /> Launch Swarm
                    </button>

                    <button
                      onClick={resetPlatform}
                      className="w-full py-1.5 border border-slate-800 text-slate-500 hover:text-slate-300 hover:border-slate-700 rounded text-[10px] font-mono uppercase tracking-wider transition-all"
                    >
                      Reset Console
                    </button>
                  </div>
                </div>

              </div>
            )}

            {activeTab === 'DIGITAL_TWIN' && <DigitalTwinSimulator />}

            {activeTab === 'ANALYTICS' && <AnalyticsDashboard />}

            {activeTab === 'SETTINGS' && (
              <div className="space-y-4 text-xs font-mono text-slate-300">
                <div className="glass-panel p-4 rounded corner-bracket relative">
                  <h3 className="text-xs uppercase text-cyber-blue tracking-wider mb-3">Edge Platform Diagnostic</h3>
                  <div className="space-y-2 text-[10px]">
                    <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                      <span className="text-slate-500">EDGE SERVER:</span>
                      <span className="text-cyber-green">ACTIVE (127.0.0.1)</span>
                    </div>
                    <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                      <span className="text-slate-500">DATABASE SYNC:</span>
                      <span className="text-cyber-green">OFFLINE LOCAL CACHE</span>
                    </div>
                    <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                      <span className="text-slate-500">AUTONOMY VERSION:</span>
                      <span className="text-white">v3.9-CBBA+ORCA-STABLE</span>
                    </div>
                    <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                      <span className="text-slate-500">CONNECTED DRONES:</span>
                      <span className="text-cyber-blue">{drones.filter(d => !d.comsLost).length} / {drones.length} UNITS</span>
                    </div>
                    <div className="flex justify-between pb-1.5">
                      <span className="text-slate-500">MAVLink CHANNELS:</span>
                      <span className="text-white">UDP 14550, 14551 ACTIVE</span>
                    </div>
                  </div>
                  <div className="corner-bracket-child"></div>
                </div>

                <div className="glass-panel p-4 rounded corner-bracket relative">
                  <h3 className="text-xs uppercase text-cyber-blue tracking-wider mb-2">Simulated Hardware Layers</h3>
                  <p className="text-[10px] text-slate-400 mb-3 leading-relaxed">
                    This browser terminal connects to a virtual PX4 SITL (Software In The Loop) simulation stack. Telemetry reports occur in sub-100ms intervals.
                  </p>
                  
                  <button 
                    onClick={() => window.location.reload()}
                    className="w-full py-2 border border-slate-800 hover:border-cyber-blue text-slate-400 hover:text-cyber-blue text-[10px] uppercase transition-all rounded flex items-center justify-center gap-1.5"
                  >
                    <RefreshCw size={10} /> Reboot SITL Stack
                  </button>
                </div>
              </div>
            )}
          </div>
          
        </aside>

        {/* CENTER INTERACTIVE TACTICAL MAP */}
        <section className="lg:col-span-6 border-r border-cyber-panelBorder/20 bg-[#03050b] flex flex-col relative overflow-hidden">
          
          {/* Map Control Overlay */}
          <div className="absolute top-3 left-3 z-10 flex gap-2">
            <div className="flex rounded border border-slate-800 bg-cyber-darkGray/80 text-[10px] font-mono overflow-hidden">
              <button 
                onClick={() => setMapStyle('GRID')}
                className={`px-3 py-1.5 ${mapStyle === 'GRID' ? 'bg-cyber-blue text-black font-bold' : 'text-slate-400 hover:text-slate-200'}`}
              >
                GRID
              </button>
              <button 
                onClick={() => setMapStyle('SATELLITE')}
                className={`px-3 py-1.5 ${mapStyle === 'SATELLITE' ? 'bg-cyber-blue text-black font-bold' : 'text-slate-400 hover:text-slate-200'}`}
              >
                SATELLITE
              </button>
              <button 
                onClick={() => setMapStyle('TERRAIN')}
                className={`px-3 py-1.5 ${mapStyle === 'TERRAIN' ? 'bg-cyber-blue text-black font-bold' : 'text-slate-400 hover:text-slate-200'}`}
              >
                TERRAIN
              </button>
            </div>

            <button
              disabled={missionStatus !== 'IDLE' && missionStatus !== 'COMPLETED'}
              onClick={() => {
                setObstaclePlacementMode(!obstaclePlacementMode);
                setDrawingMode(false);
              }}
              className={`px-3 py-1 border rounded text-[10px] font-mono uppercase tracking-wider transition-all bg-cyber-darkGray/80 ${
                obstaclePlacementMode 
                  ? 'border-cyber-orange text-cyber-orange' 
                  : 'border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              {obstaclePlacementMode ? 'Click Map to Place...' : 'Place Obstacle'}
            </button>
          </div>

          <div className="absolute top-3 right-3 z-10 bg-cyber-darkGray/75 border border-slate-800/80 px-2.5 py-1 rounded text-[10px] font-mono text-slate-400">
            MAP COORDINATES: <span className="text-white">28.6139° N, 77.2167° E</span>
          </div>

          {/* SVG Tactical map canvas */}
          <div className="flex-1 w-full h-full relative cursor-crosshair">
            
            {/* Visual satellite/terrain texture backdrop simulations */}
            {mapStyle === 'SATELLITE' && (
              <div className="absolute inset-0 bg-[#020712] opacity-40 mix-blend-color-dodge pointer-events-none">
                {/* Simulated topographical noise grid */}
                <div className="absolute inset-0 bg-[radial-gradient(#122c54_1.5px,transparent_1.5px)] [background-size:24px_24px] opacity-40"></div>
              </div>
            )}
            
            {mapStyle === 'TERRAIN' && (
              <div className="absolute inset-0 bg-[#0c120a] opacity-35 mix-blend-color-dodge pointer-events-none">
                <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(0,255,10,0.015)_1px,transparent_1px),linear-gradient(to_bottom,rgba(0,255,10,0.015)_1px,transparent_1px)] [background-size:40px_40px]"></div>
              </div>
            )}

            {/* Main SVG workspace */}
            <svg
              ref={mapContainerRef}
              onClick={handleMapClick}
              viewBox="0 0 800 500"
              className="w-full h-full bg-transparent relative z-0 select-none"
            >
              {/* Tactical Cyber Grid background lines */}
              <defs>
                <pattern id="tactical-grid-pattern" width="40" height="40" patternUnits="userSpaceOnUse">
                  <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(0, 240, 255, 0.05)" strokeWidth="1" />
                </pattern>
                <pattern id="tactical-grid-pattern-fine" width="10" height="10" patternUnits="userSpaceOnUse">
                  <path d="M 10 0 L 0 0 0 10" fill="none" stroke="rgba(0, 240, 255, 0.015)" strokeWidth="0.5" />
                </pattern>
              </defs>

              {/* Grid meshes */}
              <rect width="100%" height="100%" fill="url(#tactical-grid-pattern-fine)" />
              <rect width="100%" height="100%" fill="url(#tactical-grid-pattern)" />

              {/* Center Map Compass Ring overlay */}
              <circle cx="400" cy="230" r="140" fill="none" stroke="rgba(0, 240, 255, 0.02)" strokeWidth="1.5" strokeDasharray="5,15" />
              <circle cx="400" cy="230" r="143" fill="none" stroke="rgba(0, 240, 255, 0.02)" strokeWidth="1" />

              {/* Base Station marker */}
              <g transform={`translate(${baseStation.x}, ${baseStation.y})`} className="opacity-70">
                <circle r="14" fill="none" stroke="#00f0ff" strokeWidth="1" strokeDasharray="3 3" />
                <polygon points="0,-8 7,5 -7,5" fill="none" stroke="#00f0ff" strokeWidth="1.5" />
                <circle r="2" fill="#00f0ff" />
                <text y="20" textAnchor="middle" fill="#00f0ff" fontSize="8" fontFamily="Space Mono" opacity="0.8">BASE STATION</text>
              </g>

              {/* Boundaries (AOI polygon) */}
              {boundaries.length > 0 && (
                <g>
                  {/* Polygon outline */}
                  <polygon
                    points={boundaries.map(p => `${p.x},${p.y}`).join(' ')}
                    fill="rgba(0, 240, 255, 0.03)"
                    stroke="rgba(0, 240, 255, 0.5)"
                    strokeWidth="1.5"
                    strokeDasharray={drawingMode ? '4 4' : 'none'}
                  />
                  
                  {/* Vertices handles */}
                  {boundaries.map((pt, idx) => (
                    <g key={idx} transform={`translate(${pt.x}, ${pt.y})`}>
                      <circle r="4" fill="black" stroke="#00f0ff" strokeWidth="1.5" />
                      {idx === 0 && <circle r="8" fill="none" stroke="#00f0ff" strokeWidth="1" className="animate-ping" />}
                      <text x="6" y="-4" fill="#00f0ff" fontSize="7" fontFamily="Space Mono">P{idx + 1}</text>
                    </g>
                  ))}
                </g>
              )}

              {/* Task Nodes inside the search grid */}
              {tasks.map(task => (
                <g key={task.id} transform={`translate(${task.x}, ${task.y})`} className="opacity-90">
                  {/* Task completed state vs pending state */}
                  {task.completed ? (
                    <g>
                      <circle r="3" fill="#39ff14" />
                      <circle r="6" fill="none" stroke="#39ff14" strokeWidth="0.5" opacity="0.4" />
                    </g>
                  ) : (
                    <g>
                      <circle r="3" fill="none" stroke={task.allocatedDroneId ? 'rgba(0, 240, 255, 0.5)' : '#94a3b8'} strokeWidth="1" />
                      {/* Line connector to assignee */}
                      {task.allocatedDroneId && missionStatus === 'LAUNCHING' && (
                        <circle r="5" fill="none" stroke="#00f0ff" strokeWidth="0.5" strokeDasharray="1 3" className="animate-spin-slow" />
                      )}
                    </g>
                  )}
                </g>
              ))}

              {/* Waypoint Paths / Drone Trajectories */}
              {drones.map(drone => {
                if (drone.status === 'OFFLINE' || drone.status === 'LANDED' || drone.taskIds.length === 0) return null;
                
                // Construct the full path representation from drone's current coordinates to its remaining waypoints
                const pathPoints = [
                  { x: drone.x, y: drone.y },
                  ...drone.waypoints.slice(drone.currentTaskIndex)
                ];

                if (pathPoints.length < 2) return null;

                const pathString = pathPoints.map(p => `${p.x},${p.y}`).join(' L ');
                
                return (
                  <path
                    key={`path-${drone.id}`}
                    d={`M ${pathString}`}
                    fill="none"
                    stroke={drone.color}
                    strokeWidth="1.2"
                    strokeDasharray="3 4"
                    opacity="0.6"
                  />
                );
              })}

              {/* Dynamic Obstacles (ORCA Targets) */}
              {obstacles.map(obs => (
                <g key={obs.id} transform={`translate(${obs.x}, ${obs.y})`}>
                  {/* Radar risk boundary */}
                  <circle r={obs.radius + 30} fill="none" stroke="rgba(255, 170, 0, 0.08)" strokeWidth="0.8" strokeDasharray="2 3" />
                  
                  {/* Obstacle core body */}
                  <circle r={obs.radius} fill="rgba(30, 41, 59, 0.8)" stroke="#ffaa00" strokeWidth="1.5" className="shadow-[0_0_15px_rgba(255,170,0,0.4)]" />
                  
                  {/* Inner details */}
                  <line x1={-obs.radius + 3} y1={0} x2={obs.radius - 3} y2={0} stroke="#ffaa00" strokeWidth="0.8" opacity="0.6" />
                  <line x1={0} y1={-obs.radius + 3} x2={0} y2={obs.radius - 3} stroke="#ffaa00" strokeWidth="0.8" opacity="0.6" />
                  
                  {/* Blinking threat text */}
                  <circle r="3" fill="#ffaa00" className="animate-ping" />
                  <text y={obs.radius + 12} textAnchor="middle" fill="#ffaa00" fontSize="7" fontFamily="Space Mono" fontWeight="bold">
                    ⚠️ {obs.type}
                  </text>
                </g>
              ))}

              {/* Inter-drone conflict rings / ORCA Vectors */}
              {drones.map(d1 => {
                if (d1.status === 'OFFLINE' || d1.status === 'LANDED') return null;
                
                // If collision avoidance triggers, highlight ORCA safety vector
                return drones.map(d2 => {
                  if (d1.id >= d2.id || d2.status === 'OFFLINE' || d2.status === 'LANDED') return null;
                  
                  const dist = Math.hypot(d1.x - d2.x, d1.y - d2.y);
                  if (dist < 48) {
                    return (
                      <g key={`conflict-${d1.id}-${d2.id}`}>
                        {/* Red flashing collision link */}
                        <line 
                          x1={d1.x} 
                          y1={d1.y} 
                          x2={d2.x} 
                          y2={d2.y} 
                          stroke="#ff073a" 
                          strokeWidth="1.2" 
                          strokeDasharray="2 2"
                          className="animate-pulse-fast" 
                        />
                        <circle cx={(d1.x + d2.x)/2} cy={(d1.y + d2.y)/2} r="8" fill="rgba(255, 7, 58, 0.15)" stroke="#ff073a" strokeWidth="0.5" />
                        <text x={(d1.x + d2.x)/2 + 10} y={(d1.y + d2.y)/2 + 3} fill="#ff073a" fontSize="7" fontFamily="Space Mono" fontWeight="bold">
                          ORCA AVOID
                        </text>
                      </g>
                    );
                  }
                  return null;
                });
              })}

              {/* Live Drones renders */}
              {drones.map(drone => {
                if (drone.status === 'OFFLINE') return null;

                const isLanded = drone.status === 'LANDED';
                const hasAlert = drone.failsafeActive || drone.comsLost;

                return (
                  <g key={drone.id} transform={`translate(${drone.x}, ${drone.y})`}>
                    
                    {/* Pulsing indicator target circle */}
                    {!isLanded && (
                      <circle 
                        r="12" 
                        fill="none" 
                        stroke={drone.color} 
                        strokeWidth="0.8" 
                        opacity={drone.orcaActive ? 0.9 : 0.4} 
                        className={drone.orcaActive ? 'animate-ping' : ''} 
                      />
                    )}

                    {/* Failsafe/Low Battery Ring */}
                    {hasAlert && (
                      <circle r="16" fill="none" stroke="#ff073a" strokeWidth="1" strokeDasharray="3 3" className="animate-spin-slow" />
                    )}

                    {/* Vector Drone Icon (Rotated toward heading) */}
                    <g transform={`rotate(${Math.atan2(drone.vy, drone.vx) * (180 / Math.PI) + 90})`} className="transition-transform duration-200">
                      {/* Arms */}
                      <line x1="-8" y1="-8" x2="8" y2="8" stroke={hasAlert ? '#ff073a' : drone.color} strokeWidth="1.5" />
                      <line x1="8" y1="-8" x2="-8" y2="8" stroke={hasAlert ? '#ff073a' : drone.color} strokeWidth="1.5" />
                      
                      {/* Rotors */}
                      <circle cx="-8" cy="-8" r="2.5" fill="none" stroke={drone.color} strokeWidth="0.8" />
                      <circle cx="8" cy="-8" r="2.5" fill="none" stroke={drone.color} strokeWidth="0.8" />
                      <circle cx="-8" cy="8" r="2.5" fill="none" stroke={drone.color} strokeWidth="0.8" />
                      <circle cx="8" cy="8" r="2.5" fill="none" stroke={drone.color} strokeWidth="0.8" />

                      {/* Fuselage */}
                      <circle r="4.5" fill="#04060f" stroke={hasAlert ? '#ff073a' : drone.color} strokeWidth="1.5" />
                      <circle r="1.5" fill={hasAlert ? '#ff073a' : drone.color} />
                    </g>

                    {/* Telemetry metadata tags hanging from drone */}
                    {!isLanded && (
                      <g transform="translate(14, 4)">
                        {/* Background panel */}
                        <rect x="-2" y="-12" width="58" height="24" fill="rgba(4, 6, 15, 0.85)" stroke={hasAlert ? 'rgba(255, 7, 58, 0.3)' : 'rgba(0, 240, 255, 0.15)'} strokeWidth="0.8" rx="1" />
                        
                        {/* Text values */}
                        <text x="2" y="-4" fill="#ffffff" fontSize="7" fontFamily="Space Mono" fontWeight="bold">
                          {drone.name.substring(0, 8)}
                        </text>
                        <text x="2" y="4" fill={drone.battery < 25 ? '#ff073a' : '#94a3b8'} fontSize="6" fontFamily="Space Mono">
                          BAT:{drone.battery}% ALT:{drone.altitude}m
                        </text>
                      </g>
                    )}
                  </g>
                );
              })}

            </svg>
          </div>

          {/* Quick HUD controls overlay (bottom of map) */}
          <div className="p-3 border-t border-slate-900 bg-cyber-darkGray/60 flex items-center justify-between text-xs font-mono">
            <div className="flex gap-4">
              <div>
                <span className="text-slate-500">Fleet Active:</span>{' '}
                <span className="text-cyber-blue font-bold">
                  {drones.filter(d => d.status !== 'OFFLINE' && d.status !== 'LANDED').length} Units
                </span>
              </div>
              <div>
                <span className="text-slate-500">Tasks Completed:</span>{' '}
                <span className="text-cyber-green font-bold">
                  {tasks.filter(t => t.completed).length} / {tasks.length}
                </span>
              </div>
            </div>
            
            <div className="flex gap-2">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-cyber-blue"></span> CBBA
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-cyber-orange"></span> ORCA
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-cyber-green"></span> MAVLink Mesh
              </span>
            </div>
          </div>

        </section>

        {/* RIGHT FLEET TELEMETRY SIDEBAR */}
        <aside className="lg:col-span-3 border-l border-cyber-panelBorder/20 bg-cyber-darkGray/40 backdrop-blur-md p-4 overflow-y-auto flex flex-col space-y-4">
          
          <h2 className="text-xs font-mono text-cyber-blue uppercase tracking-widest border-b border-slate-800 pb-2 flex justify-between items-center">
            <span>📡 Telemetry Readout</span>
            <span className="text-[10px] text-slate-500 font-normal">FREQ: 5.8GHZ</span>
          </h2>

          <div className="flex-1 space-y-4">
            {drones.map(drone => {
              const completedTasksCount = tasks.filter(t => t.allocatedDroneId === drone.id && t.completed).length;

              return (
                <div 
                  key={drone.id} 
                  className={`glass-panel p-3 rounded relative transition-all border ${
                    drone.comsLost ? 'border-cyber-red/35 bg-cyber-red/5' :
                    drone.failsafeActive ? 'border-cyber-orange/30 bg-cyber-orange/5' :
                    'border-cyber-panelBorder/15'
                  }`}
                >
                  
                  {/* Status Indicator Lights */}
                  <div className="absolute top-3 right-3 flex items-center gap-1.5">
                    <span className="text-[8px] font-mono text-slate-500">SIG:</span>
                    <span className={`text-[10px] font-mono font-bold ${
                      drone.comsLost ? 'text-cyber-red' :
                      drone.signal < 50 ? 'text-cyber-orange' : 'text-cyber-green'
                    }`}>
                      {drone.comsLost ? '0%' : `${drone.signal}%`}
                    </span>
                  </div>

                  {/* Drone Name and Type */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: drone.color }}></span>
                    <h3 className="text-xs font-bold text-white tracking-wide">{drone.name}</h3>
                  </div>

                  {/* Grid of parameters */}
                  <div className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[9px] font-mono text-slate-400">
                    <div>
                      <span className="text-slate-600">STATUS:</span>{' '}
                      <span className={`font-bold ${
                        drone.comsLost ? 'text-cyber-red' :
                        drone.status === 'RTL' ? 'text-cyber-orange' : 
                        drone.status === 'MISSION' || drone.status === 'TAKEOFF' ? 'text-cyber-blue' : 'text-slate-300'
                      }`}>
                        {drone.comsLost ? 'OFFLINE' : drone.status}
                      </span>
                    </div>
                    
                    <div>
                      <span className="text-slate-600">ALTITUDE:</span>{' '}
                      <span className="text-white font-bold">{drone.altitude} m</span>
                    </div>

                    <div>
                      <span className="text-slate-600">SPEED:</span>{' '}
                      <span className="text-white font-bold">
                        {drone.status === 'IDLE' || drone.status === 'LANDED' ? '0' : `${Math.round(drone.speed * 3.6)}`} km/h
                      </span>
                    </div>

                    <div>
                      <span className="text-slate-600">TEMP:</span>{' '}
                      <span className={`font-bold ${drone.temperature > 55 ? 'text-cyber-red' : 'text-white'}`}>
                        {drone.temperature}°C
                      </span>
                    </div>

                    <div className="col-span-2">
                      <span className="text-slate-600 font-mono">PAYLOAD:</span>{' '}
                      <span className="text-cyber-blue font-bold text-[8px]">{drone.payload.toUpperCase()}</span>
                    </div>

                    <div className="col-span-2 border-t border-slate-800/80 pt-1.5 mt-1 flex justify-between items-center">
                      <span className="text-slate-600">TASKS DONE:</span>
                      <span className="text-slate-300">
                        {completedTasksCount} / {drone.taskIds.length} sectors
                      </span>
                    </div>
                  </div>

                  {/* Battery Bar widget */}
                  <div className="mt-2 space-y-0.5">
                    <div className="flex justify-between text-[8px] font-mono text-slate-500">
                      <span>BATTERY LIFE</span>
                      <span className={drone.battery < 25 ? 'text-cyber-red' : 'text-white'}>{drone.battery}%</span>
                    </div>
                    <div className="w-full bg-slate-900 h-1.5 rounded-sm overflow-hidden flex border border-slate-800">
                      <div 
                        className={`h-full transition-all duration-300 ${
                          drone.battery < 20 ? 'bg-cyber-red' :
                          drone.battery < 50 ? 'bg-cyber-orange' : 'bg-cyber-green'
                        }`} 
                        style={{ width: `${drone.battery}%` }}
                      ></div>
                    </div>
                  </div>

                  <div className="corner-bracket-child"></div>
                </div>
              );
            })}
          </div>

          {/* Quick overall Swarm KPI card */}
          <div className="glass-panel p-3 rounded border-cyber-blue/30 relative">
            <h4 className="text-[10px] font-mono text-cyber-blue uppercase tracking-wider mb-2 flex items-center justify-between">
              <span>📊 Coordination Score</span>
              <span>{swarmHealthScore}/100</span>
            </h4>
            <div className="w-full bg-slate-900 h-1.5 rounded-sm overflow-hidden flex border border-slate-800 mb-2">
              <div 
                className="h-full bg-cyber-blue shadow-cyber-blue" 
                style={{ width: `${swarmHealthScore}%` }}
              ></div>
            </div>
            <p className="text-[9px] font-mono text-slate-500 leading-relaxed">
              Consensus algorithm resolved successfully at {edgeNetwork.latencyMs}ms node delay.
            </p>
          </div>

        </aside>

      </div>

      {/* BOTTOM PANEL: LIVE SENSOR CAMERA FEEDS & LOG TIMELINE */}
      <footer className="relative z-10 border-t border-cyber-panelBorder/20 bg-cyber-darkGray/90 backdrop-blur-md h-[160px] grid grid-cols-1 lg:grid-cols-12 overflow-hidden">
        
        {/* Drone Camera feeds Viewports (5 cols) */}
        <div className="lg:col-span-5 border-r border-slate-800 p-2 flex gap-2 h-full overflow-x-auto overflow-y-hidden">
          
          {/* Feed 1: RGB stream */}
          <div className="flex-1 min-w-[120px] max-w-[170px] bg-slate-950 rounded border border-slate-800 relative overflow-hidden flex flex-col justify-between">
            <div className="absolute top-1 left-1.5 z-10 bg-black/60 border border-slate-800 px-1 rounded text-[7px] font-mono text-cyber-blue uppercase">
              CAM-01 [VALKYRIE]
            </div>

            {/* Simulated moving aerial map container */}
            <div className="absolute inset-0 bg-[#071b26] flex items-center justify-center pointer-events-none">
              {/* Scan grid effect */}
              <div className="absolute inset-0 bg-[radial-gradient(rgba(0,240,255,0.06)_1px,transparent_1px)] [background-size:12px_12px] animate-pulse"></div>
              {/* Simulated target box */}
              {missionStatus === 'ACTIVE' && (
                <div className="border border-cyber-green w-10 h-10 animate-pulse relative">
                  <span className="absolute -top-1 -left-1 text-[6px] font-mono text-cyber-green bg-black/80 px-0.5">PERSON [94%]</span>
                  <div className="absolute -top-0.5 -left-0.5 w-1 h-1 bg-cyber-green"></div>
                  <div className="absolute -bottom-0.5 -right-0.5 w-1 h-1 bg-cyber-green"></div>
                </div>
              )}
              {missionStatus !== 'ACTIVE' && (
                <div className="text-[9px] font-mono text-slate-500 uppercase tracking-widest">FEED STANDBY</div>
              )}
            </div>

            <div className="relative z-10 mt-auto bg-black/50 p-1 text-[7px] font-mono text-slate-400 border-t border-slate-900/60 flex justify-between">
              <span>RGB VIEW</span>
              <span className="text-cyber-green">● LIVE</span>
            </div>
          </div>

          {/* Feed 2: Thermal Heat signature stream */}
          <div className="flex-1 min-w-[120px] max-w-[170px] bg-slate-950 rounded border border-slate-800 relative overflow-hidden flex flex-col justify-between">
            <div className="absolute top-1 left-1.5 z-10 bg-black/60 border border-slate-800 px-1 rounded text-[7px] font-mono text-cyber-orange uppercase">
              CAM-02 [THERMAL]
            </div>

            {/* Simulated thermographic infrared feed */}
            <div className="absolute inset-0 bg-[#120521] flex items-center justify-center pointer-events-none">
              <div className="absolute inset-0 bg-[linear-gradient(rgba(255,170,0,0.04)_50%,rgba(0,0,0,0.1)_50%)] [background-size:100%_2px] animate-pulse"></div>
              {missionStatus === 'ACTIVE' && (
                <div className="w-8 h-8 rounded-full bg-gradient-to-r from-yellow-500 to-red-500 filter blur-sm animate-ping opacity-60"></div>
              )}
              {missionStatus !== 'ACTIVE' && (
                <div className="text-[9px] font-mono text-slate-500 uppercase tracking-widest">FEED STANDBY</div>
              )}
            </div>

            <div className="relative z-10 mt-auto bg-black/50 p-1 text-[7px] font-mono text-slate-400 border-t border-slate-900/60 flex justify-between">
              <span>INFRARED</span>
              <span className="text-cyber-orange">● LIVE</span>
            </div>
          </div>

          {/* Feed 3: NVDI agricultural index */}
          <div className="flex-1 min-w-[120px] max-w-[170px] bg-slate-950 rounded border border-slate-800 relative overflow-hidden flex flex-col justify-between">
            <div className="absolute top-1 left-1.5 z-10 bg-black/60 border border-slate-800 px-1 rounded text-[7px] font-mono text-cyber-green uppercase">
              CAM-03 [MULTISPECT]
            </div>

            {/* NVDI vegetation scan texture overlay */}
            <div className="absolute inset-0 bg-[#081f0b] flex items-center justify-center pointer-events-none">
              <div className="absolute inset-0 bg-[radial-gradient(rgba(57,255,20,0.05)_1px,transparent_1px)] [background-size:10px_10px]"></div>
              {missionStatus === 'ACTIVE' && (
                <div className="w-14 h-6 border border-cyber-green/40 bg-cyber-green/10 flex items-center justify-center text-[7px] text-cyber-green font-mono uppercase tracking-wide">
                  NDVI: 0.82 (OPTIMAL)
                </div>
              )}
              {missionStatus !== 'ACTIVE' && (
                <div className="text-[9px] font-mono text-slate-500 uppercase tracking-widest">FEED STANDBY</div>
              )}
            </div>

            <div className="relative z-10 mt-auto bg-black/50 p-1 text-[7px] font-mono text-slate-400 border-t border-slate-900/60 flex justify-between">
              <span>CROP NDVI</span>
              <span className="text-cyber-green">● LIVE</span>
            </div>
          </div>

        </div>

        {/* Real-time event timeline log (7 cols) */}
        <div className="lg:col-span-7 p-3 flex flex-col h-full overflow-hidden">
          
          <div className="flex items-center justify-between border-b border-slate-800 pb-1 mb-1.5 text-[9px] font-mono text-slate-500 uppercase">
            <span className="flex items-center gap-1"><Database size={10} /> Edge System Event Log Timeline</span>
            <button onClick={clearLogs} className="hover:text-cyber-red transition-colors flex items-center gap-0.5">
              <X size={10} /> Clear Logs
            </button>
          </div>

          <div className="flex-1 overflow-y-auto space-y-1.5 pr-1 select-text">
            {eventLogs.map(log => {
              let badgeColor = 'text-cyber-blue bg-cyber-blueDim border-cyber-blue/30';
              if (log.severity === 'WARNING') badgeColor = 'text-cyber-orange bg-cyber-orangeDim border-cyber-orange/30';
              if (log.severity === 'CRITICAL') badgeColor = 'text-cyber-red bg-cyber-redDim border-cyber-red/35';

              return (
                <div key={log.id} className="flex gap-2 text-[10px] font-mono leading-tight bg-slate-950/20 px-2 py-1 rounded border border-slate-900/40">
                  <span className="text-slate-600 shrink-0">{log.timestamp}</span>
                  <span className={`px-1 rounded border shrink-0 text-[8px] font-bold ${badgeColor}`}>
                    {log.category}
                  </span>
                  <span className="text-slate-300">{log.message}</span>
                </div>
              );
            })}
          </div>

        </div>

      </footer>

    </div>
  );
};
