import React from 'react';
import { useSwarmStore } from '../store/useSwarmStore';
import { BarChart2, Download, Clock, TrendingUp } from 'lucide-react';

export const AnalyticsDashboard: React.FC = () => {
  const { drones, tasks, coveragePercentage, swarmHealthScore, missionTimer, missionType } = useSwarmStore();

  // Helper formatting for mission time
  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  // Exporters
  const exportTelemetryCSV = () => {
    let csvContent = "data:text/csv;charset=utf-8,";
    csvContent += "Drone,Status,Battery(%),Altitude(m),Temperature(C),TasksAssigned,CompletedTasks\n";
    
    drones.forEach(d => {
      const completedCount = tasks.filter(t => t.allocatedDroneId === d.id && t.completed).length;
      csvContent += `${d.name},${d.status},${d.battery},${d.altitude},${d.temperature},${d.taskIds.length},${completedCount}\n`;
    });

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `dronematrx_telemetry_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const exportGeoJSON = () => {
    const geojsonData = {
      type: "FeatureCollection",
      features: drones.map(d => ({
        type: "Feature",
        properties: {
          droneId: d.id,
          name: d.name,
          battery: d.battery,
          status: d.status
        },
        geometry: {
          type: "LineString",
          coordinates: d.waypoints.map(w => [
            (w.x * 0.0001 + 77.2167).toFixed(6), // mock coordinates mapped near Delhi/India
            (28.6139 - w.y * 0.0001).toFixed(6)
          ])
        }
      }))
    };

    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(geojsonData, null, 2));
    const dlAnchorElem = document.createElement('a');
    dlAnchorElem.setAttribute("href",     dataStr     );
    dlAnchorElem.setAttribute("download", `dronematrx_paths_${Date.now()}.geojson`);
    dlAnchorElem.click();
  };

  return (
    <div className="space-y-6 text-slate-300">
      
      {/* Dynamic Key Performance Gauges */}
      <div className="grid grid-cols-2 gap-4">
        
        {/* Coverage progress */}
        <div className="glass-panel p-4 rounded corner-bracket relative flex flex-col items-center justify-center text-center">
          <h4 className="text-[10px] font-mono text-slate-400 uppercase tracking-wider mb-2">Area AOI Coverage</h4>
          <div className="relative w-24 h-24 flex items-center justify-center">
            {/* SVG Circle indicator */}
            <svg className="w-full h-full transform -rotate-90">
              <circle cx="48" cy="48" r="40" stroke="rgba(0, 240, 255, 0.08)" strokeWidth="6" fill="transparent" />
              <circle 
                cx="48" 
                cy="48" 
                r="40" 
                stroke="#00f0ff" 
                strokeWidth="6" 
                fill="transparent" 
                strokeDasharray="251.2"
                strokeDashoffset={251.2 - (251.2 * coveragePercentage) / 100}
                className="transition-all duration-500 ease-out"
              />
            </svg>
            <span className="absolute text-xl font-bold font-mono text-white">{coveragePercentage}%</span>
          </div>
          <div className="text-[9px] font-mono text-cyber-blue mt-2 uppercase">
            {tasks.filter(t => t.completed).length} / {tasks.length} SECS COVERED
          </div>
          <div className="corner-bracket-child"></div>
        </div>

        {/* Swarm Coordination Health */}
        <div className="glass-panel p-4 rounded corner-bracket relative flex flex-col items-center justify-center text-center">
          <h4 className="text-[10px] font-mono text-slate-400 uppercase tracking-wider mb-2">Decentralized Health</h4>
          <div className="relative w-24 h-24 flex items-center justify-center">
            <svg className="w-full h-full transform -rotate-90">
              <circle cx="48" cy="48" r="40" stroke="rgba(57, 255, 20, 0.08)" strokeWidth="6" fill="transparent" />
              <circle 
                cx="48" 
                cy="48" 
                r="40" 
                stroke={swarmHealthScore > 75 ? '#39ff14' : swarmHealthScore > 40 ? '#ffaa00' : '#ff073a'} 
                strokeWidth="6" 
                fill="transparent" 
                strokeDasharray="251.2"
                strokeDashoffset={251.2 - (251.2 * swarmHealthScore) / 100}
                className="transition-all duration-500 ease-out"
              />
            </svg>
            <span className="absolute text-xl font-bold font-mono text-white">{swarmHealthScore}%</span>
          </div>
          <div className="text-[9px] font-mono mt-2 uppercase" style={{ color: swarmHealthScore > 75 ? '#39ff14' : swarmHealthScore > 40 ? '#ffaa00' : '#ff073a' }}>
            SWARM STATUS: {swarmHealthScore > 75 ? 'EXCELLENT' : swarmHealthScore > 45 ? 'DEGRADED' : 'CRITICAL'}
          </div>
          <div className="corner-bracket-child"></div>
        </div>

      </div>

      {/* Flight Stats & Timers */}
      <div className="glass-panel p-4 rounded corner-bracket relative">
        <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-4 flex items-center gap-2">
          <Clock size={14} /> Mission Control Operations Log
        </h3>
        
        <div className="grid grid-cols-2 gap-4 text-xs font-mono">
          <div className="bg-cyber-darkGray/60 border border-slate-800/80 p-3 rounded">
            <span className="text-[9px] text-slate-500 block uppercase">Mission Clock</span>
            <span className="text-lg font-bold text-white tracking-widest">{formatTime(missionTimer)}</span>
          </div>
          
          <div className="bg-cyber-darkGray/60 border border-slate-800/80 p-3 rounded">
            <span className="text-[9px] text-slate-500 block uppercase">Type</span>
            <span className="text-lg font-bold text-cyber-blue uppercase tracking-wider">
              {missionType === 'NONE' ? 'N/A' : missionType}
            </span>
          </div>
        </div>
        <div className="corner-bracket-child"></div>
      </div>

      {/* Drone utilization and Task counts */}
      <div className="glass-panel p-4 rounded corner-bracket relative">
        <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-4 flex items-center gap-2">
          <BarChart2 size={14} /> Individual Drone Task Utilization
        </h3>
        
        <div className="space-y-3">
          {drones.map(d => {
            const completedTasks = tasks.filter(t => t.allocatedDroneId === d.id && t.completed).length;
            const totalTasks = d.taskIds.length;
            const utilizationPct = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
            
            return (
              <div key={d.id} className="space-y-1">
                <div className="flex justify-between text-xs font-mono">
                  <span className="text-white flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: d.color }}></span>
                    {d.name}
                  </span>
                  <span className="text-slate-400">{completedTasks} / {totalTasks} Tasks ({utilizationPct}%)</span>
                </div>
                
                <div className="w-full bg-slate-800 h-2 rounded overflow-hidden flex">
                  <div 
                    className="h-full transition-all duration-500 ease-out" 
                    style={{ 
                      width: `${utilizationPct}%`, 
                      backgroundColor: d.color,
                      boxShadow: `0 0 8px ${d.color}` 
                    }}
                  ></div>
                </div>
              </div>
            );
          })}
        </div>
        <div className="corner-bracket-child"></div>
      </div>

      {/* Exporters and sync */}
      <div className="glass-panel p-4 rounded corner-bracket relative">
        <h3 className="text-xs font-mono text-cyber-blue uppercase tracking-widest mb-3">
          <TrendingUp size={14} className="inline mr-2" /> Edge Sync & Export Modules
        </h3>
        <p className="text-[11px] text-slate-400 mb-4">
          Export local coordinate vectors and flight telemetry datasets. These profiles can be imported directly into QGroundControl or Gazebo simulator modules.
        </p>

        <div className="grid grid-cols-2 gap-2">
          <button 
            onClick={exportTelemetryCSV}
            className="flex items-center justify-center gap-1.5 border border-cyber-blue/30 hover:border-cyber-blue hover:bg-cyber-blue/5 py-2.5 rounded text-xs font-mono text-cyber-blue uppercase transition-all"
          >
            <Download size={14} /> Telemetry CSV
          </button>

          <button 
            onClick={exportGeoJSON}
            className="flex items-center justify-center gap-1.5 border border-cyber-blue/30 hover:border-cyber-blue hover:bg-cyber-blue/5 py-2.5 rounded text-xs font-mono text-cyber-blue uppercase transition-all"
          >
            <Download size={14} /> GeoJSON Paths
          </button>
        </div>
        <div className="corner-bracket-child"></div>
      </div>

    </div>
  );
};
