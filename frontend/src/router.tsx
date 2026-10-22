import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from './components/auth/RequireAuth'
import { LandingPage } from './components/LandingPage'
import { LoginPage } from './pages/LoginPage'
import { FleetPage } from './pages/FleetPage'
import { MapPage } from './pages/MapPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { MissionsPage } from './pages/MissionsPage'
import { MissionWizardPage } from './pages/MissionWizardPage'
import { SimulationPage } from './pages/SimulationPage'
import { SettingsPage } from './pages/SettingsPage'
import { StubPage } from './pages/StubPage'

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/fleet"
          element={
            <RequireAuth>
              <FleetPage />
            </RequireAuth>
          }
        />
        <Route
          path="/map"
          element={
            <RequireAuth>
              <MapPage />
            </RequireAuth>
          }
        />
        <Route
          path="/missions"
          element={
            <RequireAuth>
              <MissionsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/missions/:id/plan"
          element={
            <RequireAuth>
              <MissionWizardPage />
            </RequireAuth>
          }
        />
        <Route
          path="/missions/:id/simulate"
          element={
            <RequireAuth>
              <SimulationPage />
            </RequireAuth>
          }
        />
        <Route
          path="/analytics"
          element={
            <RequireAuth>
              <AnalyticsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/reports"
          element={
            <RequireAuth>
              <StubPage title="Reports" />
            </RequireAuth>
          }
        />
        <Route
          path="/settings"
          element={
            <RequireAuth>
              <SettingsPage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
