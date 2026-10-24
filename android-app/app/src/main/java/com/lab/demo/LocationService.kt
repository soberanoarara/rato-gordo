package com.lab.demo

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import java.util.Date

class LocationService : Service() {

    private lateinit var fused: com.google.android.gms.location.FusedLocationProviderClient
    private val handler = Handler(Looper.getMainLooper())
    private var deviceId = "android-01"

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startAsForeground()
        fused = LocationServices.getFusedLocationProviderClient(this)
        scheduleStart(5_000L)  // pós-boot: permissão pode ainda não estar efetiva
    }

    private fun startAsForeground() {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(
                "lab", "LabDemo", NotificationManager.IMPORTANCE_LOW))
        }
        val notif: Notification = NotificationCompat.Builder(this, "lab")
            .setContentTitle("LabDemo ativo")
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        } else {
            startForeground(1, notif)
        }
    }

    private fun scheduleStart(delay: Long) {
        handler.postDelayed({
            if (checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED) {
                startTracking()
            } else {
                scheduleStart(10_000L)
            }
        }, delay)
    }

    @SuppressLint("MissingPermission")
    private fun startTracking() {
        val req = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 5_000L).build()
        fused.requestLocationUpdates(req, callback, Looper.getMainLooper())
    }

    private val callback = object : LocationCallback() {
        override fun onLocationResult(r: LocationResult) {
            val loc = r.lastLocation ?: return
            HttpUploader.post(applicationContext, deviceId,
                loc.latitude, loc.longitude, loc.accuracy, Date().time)
        }
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }
}
