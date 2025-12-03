/**
 * AirScout Supabase Edge Function: check-route
 * =============================================
 * 
 * Checks a route for hazards using the 25m buffer logic.
 * Called by the PWA when a user draws or loads a route.
 * 
 * POST /functions/v1/check-route
 * Body: {
 *   route: [[lon, lat], [lon, lat], ...] | "LINESTRING(...)"
 *   buffer_meters?: number (default: 25)
 *   min_severity?: number (default: 1)
 * }
 * 
 * Response: {
 *   checked_at: string,
 *   risk_assessment: { score, level, message, hazard_count },
 *   hazards: [{ type, severity, description, distance_meters, ... }]
 * }
 */

import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

// Configuration
const DEFAULT_BUFFER_METERS = 25
const DEFAULT_MIN_SEVERITY = 1

interface RouteCheckRequest {
  route: number[][] | string  // Either coordinates array or WKT
  buffer_meters?: number
  min_severity?: number
}

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

interface RiskAssessment {
  score: number
  level: 'LOW' | 'MODERATE' | 'HIGH'
  message: string
  hazard_count: number
  highest_severity?: number
}

// Convert coordinates array to WKT LINESTRING
function coordsToWkt(coords: number[][]): string {
  const points = coords.map(([lon, lat]) => `${lon} ${lat}`).join(', ')
  return `LINESTRING(${points})`
}

// Calculate risk score from hazards
function calculateRisk(hazards: Hazard[], bufferMeters: number): RiskAssessment {
  if (hazards.length === 0) {
    return {
      score: 0,
      level: 'LOW',
      message: 'No hazards detected along this route',
      hazard_count: 0
    }
  }

  // Calculate weighted score
  let totalScore = 0
  for (const h of hazards) {
    const distanceWeight = Math.max(0, 1 - (h.distance_meters / bufferMeters))
    const severityWeight = h.severity / 5
    totalScore += distanceWeight * severityWeight * 25
  }

  const score = Math.min(100, Math.round(totalScore))
  
  let level: 'LOW' | 'MODERATE' | 'HIGH'
  let message: string

  if (score >= 70) {
    level = 'HIGH'
    message = 'High pollution risk - consider alternate route'
  } else if (score >= 40) {
    level = 'MODERATE'
    message = 'Moderate pollution risk - be aware of hazards'
  } else {
    level = 'LOW'
    message = 'Low pollution risk - route is relatively clear'
  }

  return {
    score,
    level,
    message,
    hazard_count: hazards.length,
    highest_severity: Math.max(...hazards.map(h => h.severity))
  }
}

serve(async (req) => {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  try {
    // Parse request
    const body: RouteCheckRequest = await req.json()
    
    // Validate route
    if (!body.route) {
      return new Response(
        JSON.stringify({ error: 'Missing required field: route' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    // Convert to WKT if needed
    let routeWkt: string
    if (typeof body.route === 'string') {
      routeWkt = body.route
    } else if (Array.isArray(body.route)) {
      if (body.route.length < 2) {
        return new Response(
          JSON.stringify({ error: 'Route must have at least 2 points' }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        )
      }
      routeWkt = coordsToWkt(body.route)
    } else {
      return new Response(
        JSON.stringify({ error: 'Invalid route format' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    const bufferMeters = body.buffer_meters ?? DEFAULT_BUFFER_METERS
    const minSeverity = body.min_severity ?? DEFAULT_MIN_SEVERITY

    // Create Supabase client
    const supabaseUrl = Deno.env.get('SUPABASE_URL')!
    const supabaseServiceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
    const supabase = createClient(supabaseUrl, supabaseServiceKey)

    // Query hazards within buffer using PostGIS
    // Note: This uses a raw SQL query through Supabase's rpc
    const { data: hazards, error } = await supabase.rpc('check_route_hazards', {
      route_wkt: routeWkt,
      buffer_meters: bufferMeters,
      min_severity: minSeverity
    })

    if (error) {
      console.error('Database error:', error)
      return new Response(
        JSON.stringify({ error: 'Failed to check route', details: error.message }),
        { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    // Calculate risk assessment
    const riskAssessment = calculateRisk(hazards || [], bufferMeters)

    // Build response
    const response = {
      checked_at: new Date().toISOString(),
      buffer_meters: bufferMeters,
      risk_assessment: riskAssessment,
      hazards: hazards || []
    }

    return new Response(
      JSON.stringify(response),
      { 
        status: 200, 
        headers: { ...corsHeaders, 'Content-Type': 'application/json' } 
      }
    )

  } catch (error) {
    console.error('Error:', error)
    return new Response(
      JSON.stringify({ error: 'Internal server error', details: error.message }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )
  }
})

