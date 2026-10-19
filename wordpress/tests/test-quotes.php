<?php
/**
 * Quote requests in real WordPress against a real (throwaway!) InvenTree with the plugin and shop link secret set.
 * Run: php test-quotes.php /path/to/wordpress http://127.0.0.1:8124 <shop link secret>
 */
$wordpress_dir = $argv[1];
$inventree_url = $argv[2];
$shop_secret   = $argv[3];
$_SERVER['HTTP_HOST']   = 'localhost';
$_SERVER['REQUEST_URI'] = '/';
require $wordpress_dir . '/wp-load.php';
require_once ABSPATH . 'wp-admin/includes/file.php';
require __DIR__ . '/wc-stubs.php';
require __DIR__ . '/../inventree-shop/inventree-shop.php';
do_action( 'plugins_loaded' );

$fails = 0;
function check( $label, $cond ) {
	global $fails;
	echo ( $cond ? 'ok   ' : 'FAIL ' ) . $label . "\n";
	if ( ! $cond ) { $fails++; }
}
class Redirected extends Exception {}
add_filter( 'wp_redirect', function ( $location ) { throw new Redirected( $location ); } );
$GLOBALS['mails'] = array();
add_filter( 'pre_wp_mail', function ( $null, $atts ) { $GLOBALS['mails'][] = $atts; return true; }, 10, 2 );

update_option( 'inventree_shop_inventree_url', $inventree_url );
update_option( 'inventree_shop_secret', $shop_secret );
update_option( 'inventree_shop_sample_address', "OMG Harness\n1 Workshop Rd\nKalgoorlie WA 6430" );
update_option( 'inventree_shop_phone', '08 1234 5678' );
update_option( 'inventree_shop_notify_email', 'tyler@omg.example' );
$page_id = wp_insert_post( array( 'post_type' => 'page', 'post_status' => 'publish', 'post_title' => 'Request a quote', 'post_content' => '[inventree_quote_request]' ) );
update_option( 'inventree_shop_quote_page', $page_id );

check( 'connected to InvenTree (http allowed only for localhost)', InvenTree_Link::configured() );
check( 'https or localhost only', InvenTree_Link::allowed_url( 'https://x.com' ) && ! InvenTree_Link::allowed_url( 'http://evil.com' ) );

// logged out: no form
wp_set_current_user( 0 );
check( 'logged-out visitors are asked to log in', false !== strpos( do_shortcode( '[inventree_quote_request]' ), 'log in or create an account' ) );

$user = username_exists( 'quoter' ) ? username_exists( 'quoter' ) : wp_create_user( 'quoter', 'pw', 'quoter@acme.example' );
update_user_meta( $user, 'billing_phone', '0400 111 222' );
update_user_meta( $user, 'billing_company', 'Acme Mining' );
update_user_meta( $user, 'billing_first_name', 'Quinn' );
wp_set_current_user( $user );

// prefilled from a product page
$GLOBALS['fake_products'][501] = new Fake_Product( 'DT06-2S', 'DT06-2S Deutsch plug' );
$_GET['based_on'] = 'DT06-2S';
$form = do_shortcode( '[inventree_quote_request]' );
unset( $_GET['based_on'] );
check( 'form prefilled from "Request a custom version"', false !== strpos( $form, 'A custom version of DT06-2S Deutsch plug' ) && false !== strpos( $form, 'value="DT06-2S"' ) );
check( 'form has item rows, file upload and the three ways to show us', false !== strpos( $form, 'item_files_0[]' ) && false !== strpos( $form, '[provide][sample]' ) && false !== strpos( $form, '[provide][call]' ) );

function tmp_file( $content ) {
	$path = wp_tempnam( 'quote' );
	file_put_contents( $path, $content );
	return $path;
}
function submit_form( array $post, array $files ) {
	$_SERVER['REQUEST_METHOD'] = 'POST';
	$_POST  = array_merge( array( 'inventree_action' => 'quote_submit', 'inventree_quote_nonce' => wp_create_nonce( 'inventree_quote_submit' ) ), $post );
	$_FILES = $files;
	try {
		InvenTree_Shop_Quotes::handle_post();
	} catch ( Redirected $r ) {
		return $r->getMessage();
	} finally {
		$_POST = array(); $_FILES = array(); $_SERVER['REQUEST_METHOD'] = 'GET';
	}
	return null;
}

// a custom harness with nothing to show us is refused, before anything is sent
$redirect = submit_form( array( 'items' => array( array( 'kind' => 'custom', 'description' => 'Loom', 'quantity' => 1 ) ) ), array() );
$form     = do_shortcode( '[inventree_quote_request]' );
check( 'custom item without drawing/sample/call is refused with a clear message', null === $redirect && false !== strpos( $form, 'upload a layout drawing or photos, send us a sample, or ask us to call' ) );
check( 'refused form keeps what the customer typed', false !== strpos( $form, '>Loom</textarea>' ) );

$redirect = submit_form( array( 'items' => array( array( 'kind' => 'component', 'oem_part_number' => 'X', 'quantity' => 1 ) ) ),
	array( 'item_files_0' => array( 'name' => array( 'evil.exe' ), 'tmp_name' => array( tmp_file( 'MZ' ) ), 'error' => array( 0 ), 'size' => array( 2 ), 'type' => array( '' ) ) ) );
check( 'disallowed file type refused', null === $redirect && false !== strpos( do_shortcode( '[inventree_quote_request]' ), "evil.exe: that type of file" ) );

// a real submission with a drawing, a sample and a call
$drawing  = "%PDF-1.4 real layout drawing\n" . str_repeat( 'x', 5000 );
$mails_before = count( $GLOBALS['mails'] );
$redirect = submit_form(
	array(
		'phone' => '0400 111 222', 'company' => 'Acme Mining', 'required_by' => '2026-12-15', 'delivery_postcode' => '6430', 'notes' => 'Urgent-ish',
		'items' => array(
			array( 'kind' => 'custom', 'description' => 'Engine loom from sample', 'machine' => 'CAT R1700', 'quantity' => 2, 'provide' => array( 'sample' => 1 ) ),
			array( 'kind' => 'copy', 'description' => 'Cab harness', 'quantity' => 1, 'provide' => array( 'drawings' => 1, 'call' => 1 ), 'call_time' => 'After 2pm' ),
			array( 'kind' => 'component', 'oem_part_number' => 'DT06-2S', 'quantity' => 20 ),
		),
	),
	array( 'item_files_1' => array( 'name' => array( 'cab layout.pdf' ), 'tmp_name' => array( tmp_file( $drawing ) ), 'error' => array( 0 ), 'size' => array( strlen( $drawing ) ), 'type' => array( 'application/pdf' ) ) )
);
check( 'valid request redirects to My Account > Quotes', $redirect && preg_match( '#/my-account/quotes/(QR-\d{6}-[A-Z0-9]{4})/\?submitted=1#', $redirect, $m ) );
$reference = isset( $m[1] ) ? $m[1] : '';
$quotes    = InvenTree_Shop_Quotes::fetch_quotes( $user );
$mine      = is_array( $quotes ) ? array_values( array_filter( $quotes, function ( $q ) use ( $reference ) { return $q['reference'] === $reference; } ) ) : array();
check( 'InvenTree has the request (signed list)', 1 === count( $mine ) );
if ( $mine ) {
	$q = $mine[0];
	check( 'three items, in order', 3 === count( $q['items'] ) && 'Engine loom from sample' === $q['items'][0]['description'] && 20 === $q['items'][2]['quantity'] );
	check( 'status: waiting for the sample', 'waiting_sample' === $q['status'] );
	check( 'drawing arrived on item 2 intact', 1 === count( $q['files'] ) && 'cab-layout.pdf' === $q['files'][0]['name'] && strlen( $drawing ) === $q['files'][0]['size'] && $q['files'][0]['item_id'] === $q['items'][1]['id'] );
	check( 'call details kept', $q['items'][1]['wants_call'] && 'After 2pm' === $q['items'][1]['call_time'] );
	check( 'customer view has no prices yet', array() === $q['lines'] && null === $q['total'] );
}
$new_mails = array_slice( $GLOBALS['mails'], $mails_before );
$to_customer = array_values( array_filter( $new_mails, function ( $m ) { return 'quoter@acme.example' === $m['to']; } ) );
$to_owner    = array_values( array_filter( $new_mails, function ( $m ) { return 'tyler@omg.example' === $m['to']; } ) );
check( 'customer emailed with the sample label link', $to_customer && false !== strpos( $to_customer[0]['message'], '/label' ) && false !== strpos( $to_customer[0]['subject'], $reference ) );
check( 'shop owner emailed', $to_owner && false !== strpos( $to_owner[0]['message'], '3 item(s), 1 sample(s), 1 call(s), 1 file(s)' ) );

// My Account pages
ob_start(); InvenTree_Shop_Quotes::account_page( '' ); $list = ob_get_clean();
check( 'My Account list shows the request', false !== strpos( $list, $reference ) && false !== strpos( $list, 'Waiting for sample' ) );
ob_start(); InvenTree_Shop_Quotes::account_page( $reference ); $detail = ob_get_clean();
check( 'detail page: items, sample status, label button, working message', false !== strpos( $detail, 'sample to send' ) && false !== strpos( $detail, 'Print sample label' ) && false !== strpos( $detail, "working on your quote" ) );

// a quoted quote renders the accept form (fixture - pricing is done in InvenTree)
$render = new ReflectionMethod( 'InvenTree_Shop_Quotes', 'render_detail' );
$render->setAccessible( true );
ob_start();
$render->invoke( null, array( 'reference' => 'QR-FIX-0001', 'status' => 'quoted', 'status_label' => 'Quoted', 'valid_until' => '2026-11-18', 'decline_reason' => '', 'currency' => 'AUD', 'can_accept' => true, 'total' => '2942.10',
	'items' => array( array( 'quantity' => 2, 'kind_label' => 'Custom harness', 'description' => 'Loom', 'oem_part_number' => '', 'sending_sample' => false, 'sample_received' => false, 'wants_call' => false, 'called' => false, 'state' => 'quoted', 'cannot_reason' => '' ) ),
	'lines' => array( array( 'id' => 7, 'description' => 'Engine loom', 'quantity' => 2, 'unit_price' => '1450.00', 'total' => '2900.00', 'lead_time' => '6 weeks' ) ) ) );
$quoted = ob_get_clean();
check( 'quoted page: tick-box per line, totals ex GST, lead time, accept button', false !== strpos( $quoted, 'name="line_ids[]" value="7"' ) && false !== strpos( $quoted, 'Total (ex GST)' ) && false !== strpos( $quoted, '6 weeks' ) && false !== strpos( $quoted, 'Accept and continue to payment' ) );

// accepting a quote that isn't priced yet is refused by InvenTree, shown to the customer
$_SERVER['REQUEST_METHOD'] = 'POST';
$_POST = array( 'inventree_action' => 'quote_accept', 'reference' => $reference, 'line_ids' => array( 1 ), 'inventree_quote_nonce' => wp_create_nonce( 'inventree_quote_accept_' . $reference ) );
$GLOBALS['fake_notices'] = array();
try { InvenTree_Shop_Quotes::handle_post(); } catch ( Redirected $r ) { $GLOBALS['fake_notices'][] = 'REDIRECTED ' . $r->getMessage(); }
$_POST = array(); $_SERVER['REQUEST_METHOD'] = 'GET';
check( 'accepting an unpriced quote is refused with InvenTree\'s reason', $GLOBALS['fake_notices'] && false !== strpos( $GLOBALS['fake_notices'][0], "can't be accepted" ) );

// sample label
$label = InvenTree_Shop_Quotes::label_html( array( 'reference' => $reference, 'items' => $mine ? $mine[0]['items'] : array() ), array( 'name' => 'Quinn', 'company' => 'Acme Mining', 'phone' => '0400 111 222' ) );
check( 'sample label: reference, our address, who it\'s from, the sample item', false !== strpos( $label, $reference ) && false !== strpos( $label, 'Kalgoorlie WA 6430' ) && false !== strpos( $label, 'Quinn - Acme Mining' ) && false !== strpos( $label, 'Engine loom from sample' ) && false === strpos( $label, 'Cab harness' ) );

// InvenTree down: queued, then delivered by the retry
update_option( 'inventree_shop_inventree_url', 'http://127.0.0.1:1' );
$redirect = submit_form( array( 'items' => array( array( 'kind' => 'copy', 'description' => 'Rear loom', 'quantity' => 1, 'provide' => array( 'drawings' => 1 ) ) ) ),
	array( 'item_files_0' => array( 'name' => array( 'rear.pdf' ), 'tmp_name' => array( tmp_file( '%PDF rear' ) ), 'error' => array( 0 ), 'size' => array( 9 ), 'type' => array( 'application/pdf' ) ) ) );
preg_match( '#/quotes/(QR-[^/]+)/#', (string) $redirect, $m2 );
$queued_ref = isset( $m2[1] ) ? $m2[1] : '';
$queue = get_option( 'inventree_shop_quote_queue', array() );
check( 'InvenTree unreachable: customer still gets a reference, request queued with its file', $queued_ref && isset( $queue[ $queued_ref ] ) && file_exists( $queue[ $queued_ref ]['files'][0]['path'] ) );
ob_start(); InvenTree_Shop_Quotes::account_page( '' ); $list = ob_get_clean();
check( 'queued request shows as "Sending" in My Account', false !== strpos( $list, $queued_ref ) && false !== strpos( $list, 'Sending' ) );
$kept_file = $queue[ $queued_ref ]['files'][0]['path'];
update_option( 'inventree_shop_inventree_url', $inventree_url );
InvenTree_Shop_Quotes::retry_queue();
$queue  = get_option( 'inventree_shop_quote_queue', array() );
$quotes = InvenTree_Shop_Quotes::fetch_quotes( $user );
$refs   = is_array( $quotes ) ? wp_list_pluck( $quotes, 'reference' ) : array();
check( 'retry delivered it to InvenTree and cleaned up', ! isset( $queue[ $queued_ref ] ) && in_array( $queued_ref, $refs, true ) && ! file_exists( $kept_file ) );

// notifications from InvenTree
do_action( 'rest_api_init' );
function notify( array $data, $secret ) {
	$body = wp_json_encode( $data );
	$ts   = (string) time();
	$sig  = base64_encode( hash_hmac( 'sha256', $ts . '.' . $body, $secret, true ) );
	$req  = new WP_REST_Request( 'POST', '/inventree-shop/v1/notify' );
	$req->set_body( $body );
	$req->set_header( 'Content-Type', 'application/json' );
	$req->set_header( 'X-InvenTree-Shop-Timestamp', $ts );
	$req->set_header( 'X-InvenTree-Shop-Signature', $sig );
	return rest_do_request( $req );
}
$before = count( $GLOBALS['mails'] );
$res = notify( array( 'event' => 'quote_sent', 'reference' => $reference, 'customer_id' => $user, 'total' => '2942.10', 'currency' => 'AUD', 'valid_until' => '2026-11-18' ), $shop_secret );
$mail = end( $GLOBALS['mails'] );
check( 'quote ready notification emails the customer', 200 === $res->get_status() && count( $GLOBALS['mails'] ) === $before + 1 && 'quoter@acme.example' === $mail['to'] && false !== strpos( $mail['subject'], 'is ready' ) && false !== strpos( $mail['message'], '18 Nov 2026' ) );
$res = notify( array( 'event' => 'quote_declined', 'reference' => $reference, 'customer_id' => $user, 'reason' => 'We can\'t source the connector.' ), $shop_secret );
$mail = end( $GLOBALS['mails'] );
check( 'declined notification includes the reason and phone', false !== strpos( $mail['message'], 'source the connector' ) && false !== strpos( $mail['message'], '08 1234 5678' ) );
$before = count( $GLOBALS['mails'] );
$res = notify( array( 'event' => 'quote_sent', 'reference' => $reference, 'customer_id' => $user ), 'wrong-secret' );
check( 'notification with a bad signature is refused', in_array( $res->get_status(), array( 401, 403 ), true ) && count( $GLOBALS['mails'] ) === $before );
wp_set_current_user( 0 );
$res = notify( array( 'event' => 'quote_sent', 'reference' => $reference, 'customer_id' => $user ), 'wrong-secret' );
check( '...also when nobody is logged in', 401 === $res->get_status() && count( $GLOBALS['mails'] ) === $before );
$old = notify( array( 'event' => 'quote_sent', 'reference' => $reference, 'customer_id' => $user ), $shop_secret );
wp_set_current_user( $user );

// quote-only products
$quote_only = new Fake_Product( 'OMG-CUSTOM-1', 'Custom loom', array( '_inventree_quote_only' => '1' ) );
$normal     = new Fake_Product( 'DT06-2S', 'Plug', array( '_inventree_quote_only' => '0' ) );
$quote_order = new Fake_Order( 900, $user, 10 ); $quote_order->meta['inventree_quote'] = 'QR-X';
$plain_order = new Fake_Order( 901, $user, 10 );
check( 'quote-only product can\'t be bought from the catalogue', false === apply_filters( 'woocommerce_is_purchasable', true, $quote_only ) && true === apply_filters( 'woocommerce_is_purchasable', true, $normal ) );
check( '...but can be paid for on its quote\'s order', true === apply_filters( 'woocommerce_pay_order_product_is_purchasable', false, $quote_only, $quote_order ) && false === apply_filters( 'woocommerce_pay_order_product_is_purchasable', false, $quote_only, $plain_order ) );
check( '...even if it has no stock', true === apply_filters( 'woocommerce_pay_order_product_in_stock', false, $quote_only, $quote_order ) );
global $product;
$product = $normal;
ob_start(); InvenTree_Shop_Quotes::custom_version_button(); $btn = ob_get_clean();
$product = $quote_only;
ob_start(); InvenTree_Shop_Quotes::custom_version_button(); $no_btn = ob_get_clean();
check( '"Request a custom version" on normal products only', false !== strpos( $btn, 'based_on=DT06-2S' ) && '' === $no_btn );

echo $fails ? "\n$fails FAILED\n" : "\nALL QUOTE CHECKS PASSED\n";
exit( $fails ? 1 : 0 );
