/**
 * AirScout Supabase Edge Function: check-route
 * =============================================
 *
 * Checks a route for hazards using the 25m buffer logic.
 * Also supports safe-route finding via OSRM integration.
 *
 * POST /functions/v1/check-route
 * Body: {
 *   route: [[lon, lat], ...] | "LINESTRING(...)"
 *   buffer_meters?: number
 *   min_severity?: number
 *   mode?: "check" | "safe-route"
 *   origin?: [lon, lat]
 *   destination?: [lon, lat]
 * }
 */

import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

const RISK_WEIGHT_MULTIPLIER = 25
const RISK_THRESHOLD_HIGH = 70
const RISK_THRESHOLD_MODERATE = 40
const DEFAULT_BUFFER = 25
const DEFAULT_MIN_SEVERITY = 1

interface Hazard {
  id: string
  type: string
  severity: number
  description: string
  source_id: string
  longitude: number
  latitude: number
  distance_meters: number
  expires_at: string
}

function coordsToWkt(coords: number[][]): string {
  return `LINESTRING(${coords.map(([lon, lat]) => `${lon} ${lat}`).join(', ')})`
}

function calculateRisk(hazards: Hazard[], bufferMeters: number) {
  if (hazards.length === 0) {
    return { score: 0, level: 'LOW' as const, message: 'No hazards detected along this route', hazard_count: 0 }
  }

  let total = 0
  for (const h of hazards) {
    const dw = Math.max(0, 1 - (h.distance_meters / bufferMeters))
    const sw = h.severity / 5
    total += dw * sw * RISK_WEIGHT_MULTIPLIER
  }

  const score = Math.min(100, Math.round(total))
  const level = score >= RISK_THRESHOLD_HIGH ? 'HIGH' : score >= RISK_THRESHOLD_MODERATE ? 'MODERATE' : 'LOW'
  const messages = {
    HIGH: 'High pollution risk — consider alternate route',
    MODERATE: 'Moderate pollution risk — be aware of hazards',
    LOW: 'Low pollution risk — route is relatively clear',
  }

  return {
    score, level, message: messages[level],
    hazard_count: hazards.length,
    highest_severity: Math.max(...hazards.map(h => h.severity)),
  }
}

async function getOsrmRoutes(origin: number[], destination: number[], alternatives = 3) {
  const coords = `${origin[0]},${origin[1]};${destination[0]},${destination[1]}`
  const url = `https://router.project-osrm.org/route/v1/foot/${coords}?overview=full&geometries=geojson&alternatives=true`

  const resp = await fetch(url)
  const data = await resp.json()

  if (data.code !== 'Ok') return []

  return data.routes.slice(0, alternatives).map((r: any) => ({
    coordinates: r.geometry.coordinates,
    distance_m: r.distance,
    duration_s: r.duration,
  }))
}

serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  try {
    const body = await req.json()

    const supabaseUrl = Deno.env.get('SUPABASE_URL')!
    const supabaseServiceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
    const supabase = createClient(supabaseUrl, supabaseServiceKey)

    const bufferMeters = body.buffer_meters ?? DEFAULT_BUFFER
    const minSeverity = body.min_severity ?? DEFAULT_MIN_SEVERITY

    if (body.mode === 'safe-route' && body.origin && body.destination) {
      const routes = await getOsrmRoutes(body.origin, body.destination)
      if (routes.length === 0) {
        return new Response(
          JSON.stringify({ error: 'No routes found' }),
          { status: 404, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        )
      }

      const rankedRoutes = []
      for (const route of routes) {
        const wkt = coordsToWkt(route.coordinates)
        const { data: hazards, error } = await supabase.rpc('check_route_hazards', {
          route_wkt: wkt, buffer_meters: bufferMeters, min_severity: minSeverity,
        })
        const risk = calculateRisk(hazards || [], bufferMeters)
        rankedRoutes.push({ ...route, hazards: hazards || [], risk_assessment: risk })
      }

      rankedRoutes.sort((a, b) => a.risk_assessment.score - b.risk_assessment.score)

      return new Response(
        JSON.stringify({ checked_at: new Date().toISOString(), routes: rankedRoutes, recommended: rankedRoutes[0] }),
        { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    if (!body.route) {
      return new Response(
        JSON.stringify({ error: 'Missing required field: route' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    let routeWkt: string
    if (typeof body.route === 'string') {
      routeWkt = body.route
    } else if (Array.isArray(body.route) && body.route.length >= 2) {
      routeWkt = coordsToWkt(body.route)
    } else {
      return new Response(
        JSON.stringify({ error: 'Invalid route format' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    const { data: hazards, error } = await supabase.rpc('check_route_hazards', {
      route_wkt: routeWkt, buffer_meters: bufferMeters, min_severity: minSeverity,
    })

    if (error) {
      return new Response(
        JSON.stringify({ error: 'Failed to check route', details: error.message }),
        { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    const riskAssessment = calculateRisk(hazards || [], bufferMeters)

    return new Response(
      JSON.stringify({
        checked_at: new Date().toISOString(),
        buffer_meters: bufferMeters,
        risk_assessment: riskAssessment,
        hazards: hazards || [],
      }),
      { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )

  } catch (error) {
    return new Response(
      JSON.stringify({ error: 'Internal server error', details: (error as Error).message }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )
  }
})
