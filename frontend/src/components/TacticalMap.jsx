import React, { useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';

// Placeholder for military-grade Mapbox key
mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN;

const TacticalMap = ({ lakes }) => {
  const mapContainer = useRef(null);
  const map = useRef(null);

  useEffect(() => {
    if (map.current) return;
    map.current = new mapboxgl.Map({
      container: mapContainer.current,
      style: 'mapbox://styles/mapbox/satellite-streets-v12',
      center: [74.5, 36.5],
      zoom: 8,
      pitch: 60, // 3D View
      bearing: -17.6
    });

    map.current.on('load', () => {
      // Add terrain source and layer for 3D effect
      map.current.addSource('mapbox-dem', {
        'type': 'raster-dem',
        'url': 'mapbox://mapbox.mapbox-terrain-dem-v1',
        'tileSize': 512,
        'maxzoom': 14
      });
      map.current.setTerrain({ 'source': 'mapbox-dem', 'exaggeration': 1.5 });
    });
  }, [lakes]);

  return <div ref={mapContainer} style={{ height: '100vh', width: '100%' }} />;
};

export default TacticalMap;
