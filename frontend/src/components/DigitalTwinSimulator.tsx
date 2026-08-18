import React from 'react';
import { useSwarmStore } from '../store/useSwarmStore';
import { Wind, CloudRain, EyeOff, ShieldAlert, ZapOff, RefreshCw, Plus, Trash2 } from 'lucide-react';

export const DigitalTwinSimulator: React.FC = () => {
  const { 
    drones, 
    obstacles, 
    weather, 
    setWeather, 
    addObstacle, 
    clearObstacles, 
    injectComsLoss, 
    injectLowBattery, 
    restoreDrone
  } = useSwarmStore();

  const handleWindChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setWeather(Number(e.target.value), weather.rainMm, weather.fogDensityPct);
  };

  const handleRainChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setWeather(weather.windSpeedKph, Number(e.target.value), weather.fogDensityPct);
  };

  const handleFogChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setWeather(weather.windSpeedKph, weather.rainMm, Number(e.target.value));
  };

  const handleAddRandomObstacle = () => {
    const types: ('CRANE' | 'BIRD' | 'STRUCTURE' | 'STORM')[] = ['CRANE', 'BIRD', 'STRUCTURE', 'STORM'];
    const type = types[Math.floor(Math.random() * types.length)];
    const x = 150 + Math.random() * 500;
    const y = 80 + Math.random() * 300;
    const radius = 15 + Math.random() * 20;

    addObstacle({
      id: `obs-${Date.now()}`,
      x,
      y,
      radius,
      type
    });
  };

  return (
    <div className="space-y-6 text-slate-300">
      
      {/* Weather Parameters (SITL Environment) */}
      <div className="glass-panel p-4 rounded corner-bracket relative">
        <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-4 flex items-center gap-2">
          <Wind size={14} className="text-cyber-blue animate-pulse" /> Environmental Controls (SITL)
        </h3>
        
        <div className="space-y-4">
          <div>
            <div className="flex justify-between text-xs font-mono mb-1">
              <span className="flex items-center gap-1"><Wind size={12} /> Wind Speed</span>
              <span className={weather.windSpeedKph > 25 ? 'text-cyber-orange' : 'text-cyber-blue'}>
                {weather.windSpeedKph} km/h
              </span>
            </div>
            <input 
              type="range" 
              min="0" 
              max="55" 
              value={weather.windSpeedKph} 
              onChange={handleWindChange}
              className="w-full accent-cyber-blue h-1 bg-slate-800 rounded-lg appearance-none cursor-pointer"
            />
            {weather.windSpeedKph > 25 && (
              <span className="text-[10px] font-mono text-cyber-orange block mt-1">⚠️ Warning: High wind limits max flight speed.</span>
            )}
          </div>

          <div>
            <div className="flex justify-between text-xs font-mono mb-1">
              <span className="flex items-center gap-1"><CloudRain size={12} /> Precipitation</span>
              <span className={weather.rainMm > 8 ? 'text-cyber-red' : 'text-cyber-blue'}>
                {weather.rainMm} mm/h
              </span>
            </div>
            <input 
              type="range" 
              min="0" 
              max="20" 
              value={weather.rainMm} 
              onChange={handleRainChange}
              className="w-full accent-cyber-blue h-1 bg-slate-800 rounded-lg appearance-none cursor-pointer"
            />
            {weather.rainMm > 8 && (
              <span className="text-[10px] font-mono text-cyber-red block mt-1">⚠️ Critical: High rain increases battery depletion rates.</span>
            )}
          </div>

          <div>
            <div className="flex justify-between text-xs font-mono mb-1">
              <span className="flex items-center gap-1"><EyeOff size={12} /> Fog / Density</span>
              <span className="text-cyber-blue">{weather.fogDensityPct}%</span>
            </div>
            <input 
              type="range" 
              min="0" 
              max="100" 
              value={weather.fogDensityPct} 
              onChange={handleFogChange}
              className="w-full accent-cyber-blue h-1 bg-slate-800 rounded-lg appearance-none cursor-pointer"
            />
          </div>
        </div>
        <div className="corner-bracket-child"></div>
      </div>

      {/* Obstacle Injection Tool */}
      <div className="glass-panel p-4 rounded corner-bracket relative">
        <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-4 flex items-center gap-2">
          <ShieldAlert size={14} /> Obstacle Injection (ORCA testing)
        </h3>

        <p className="text-[11px] text-slate-400 mb-4 leading-relaxed">
          Inject temporary structures, storms, or cranes into the airspace. The swarm will recalculate local velocity paths in real-time to avoid collisions using ORCA.
        </p>

        <div className="flex gap-2">
          <button 
            onClick={handleAddRandomObstacle}
            className="flex-1 flex items-center justify-center gap-1.5 border border-cyber-blue/30 hover:border-cyber-blue bg-cyber-blue/5 hover:bg-cyber-blue/10 px-3 py-2 rounded text-xs font-mono text-cyber-blue uppercase transition-all"
          >
            <Plus size={14} /> Inject Obstacle
          </button>
          <button 
            onClick={clearObstacles}
            disabled={obstacles.length === 0}
            className="border border-cyber-red/30 hover:border-cyber-red bg-cyber-red/5 hover:bg-cyber-red/10 px-3 py-2 rounded text-xs font-mono text-cyber-red uppercase transition-all disabled:opacity-40 disabled:pointer-events-none"
          >
            <Trash2 size={14} />
          </button>
        </div>

        {obstacles.length > 0 && (
          <div className="mt-4 border-t border-slate-800/80 pt-3">
            <h4 className="text-[10px] font-mono text-slate-400 uppercase mb-2">Active Airspace Obstacles ({obstacles.length})</h4>
            <div className="max-h-24 overflow-y-auto space-y-1.5 pr-1">
              {obstacles.map(obs => (
                <div key={obs.id} className="flex justify-between items-center text-[10px] font-mono bg-cyber-darkGray/60 border border-slate-800/60 p-1.5 rounded">
                  <span className="text-cyber-orange">{obs.type}</span>
                  <span className="text-slate-500">POS: ({Math.round(obs.x)}, {Math.round(obs.y)}) | R: {Math.round(obs.radius)}m</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="corner-bracket-child"></div>
      </div>

      {/* Drone Hardware / Failsafe Jammer */}
      <div className="glass-panel p-4 rounded corner-bracket relative">
        <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-4 flex items-center gap-2">
          <ZapOff size={14} /> Failsafe & Jamming Simulation
        </h3>
        
        <p className="text-[11px] text-slate-400 mb-4 leading-relaxed">
          Select individual drones to simulate hardware or communication failures. The edge platform automatically deploys countermeasures (LoRa fallbacks or Return-to-Home failsafes).
        </p>

        <div className="space-y-3">
          {drones.map(drone => (
            <div key={drone.id} className="border border-slate-800 bg-cyber-darkGray/30 p-2.5 rounded flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-white flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: drone.color }}></span>
                  {drone.name}
                </span>
                <span className={`text-[9px] font-mono border px-1.5 rounded ${
                  drone.comsLost ? 'border-cyber-red bg-cyber-red/5 text-cyber-red' :
                  drone.failsafeActive ? 'border-cyber-orange bg-cyber-orange/5 text-cyber-orange' :
                  'border-cyber-green bg-cyber-green/5 text-cyber-green'
                }`}>
                  {drone.comsLost ? 'SIGNAL LOST' : drone.failsafeActive ? 'FAILSAFE RTL' : drone.status}
                </span>
              </div>

              <div className="grid grid-cols-3 gap-1.5">
                <button
                  disabled={drone.comsLost}
                  onClick={() => injectComsLoss(drone.id)}
                  className="flex items-center justify-center gap-1 border border-cyber-red/30 hover:border-cyber-red hover:bg-cyber-red/5 p-1 rounded text-[9px] font-mono text-cyber-red uppercase transition-all disabled:opacity-40"
                >
                  <EyeOff size={10} /> Jam RF
                </button>

                <button
                  disabled={drone.failsafeActive || drone.battery <= 15}
                  onClick={() => injectLowBattery(drone.id)}
                  className="flex items-center justify-center gap-1 border border-cyber-orange/30 hover:border-cyber-orange hover:bg-cyber-orange/5 p-1 rounded text-[9px] font-mono text-cyber-orange uppercase transition-all disabled:opacity-40"
                >
                  <ZapOff size={10} /> Kill Bat
                </button>

                <button
                  onClick={() => restoreDrone(drone.id)}
                  className="flex items-center justify-center gap-1 border border-cyber-blue/30 hover:border-cyber-blue hover:bg-cyber-blue/5 p-1 rounded text-[9px] font-mono text-cyber-blue uppercase transition-all"
                >
                  <RefreshCw size={10} /> Restore
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="corner-bracket-child"></div>
      </div>

    </div>
  );
};
